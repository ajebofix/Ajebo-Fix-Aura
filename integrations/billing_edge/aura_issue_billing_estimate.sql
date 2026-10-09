-- Runs as the existing Billing Edge function's service-role database identity.
-- Transactional issuance; no client publication, email send, or payment changes.
CREATE OR REPLACE FUNCTION public.aura_issue_billing_estimate(
  p_document_id uuid,
  p_client_id uuid,
  p_vehicle_id uuid,
  p_job_id uuid,
  p_aura_car_id integer,
  p_aura_owner_user_id integer,
  p_aura_advisor_user_id integer,
  p_expected_revision integer,
  p_expected_total numeric,
  p_expected_updated_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = 'public', 'pg_temp'
AS $function$
DECLARE
  v_id uuid;
  v_number text;
BEGIN
  IF p_aura_car_id < 1 OR p_aura_owner_user_id < 1 OR
     p_aura_advisor_user_id < 1 OR p_expected_revision < 1 OR
     p_expected_total <= 0 OR p_expected_updated_at IS NULL
  THEN
    RETURN false;
  END IF;

  UPDATE public.billing_documents AS d
    SET status = 'issued', updated_at = clock_timestamp()
  WHERE d.id = p_document_id
    AND d.client_id = p_client_id
    AND d.vehicle_id = p_vehicle_id
    AND d.job_id = p_job_id
    AND d.doc_type = 'estimate'
    AND d.status = 'draft'
    AND d.created_by IS NOT NULL
    AND d.superseded_at IS NULL
    AND d.share_revoked_at IS NULL
    AND coalesce(d.is_legacy_import, false) IS FALSE
    AND d.total = p_expected_total
    AND d.revision_no = p_expected_revision
    AND d.updated_at = p_expected_updated_at
    AND nullif(trim(d.terms), '') IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM public.billing_jobs j
      WHERE j.id = p_job_id
        AND j.client_id = p_client_id
        AND j.vehicle_id = p_vehicle_id
        AND d.job_snapshot->>'version' = j.version::text
    )
    AND EXISTS (
      SELECT 1 FROM public.aura_billing_publication_grants g
      WHERE g.billing_client_id = p_client_id
        AND g.billing_vehicle_id = p_vehicle_id
        AND g.aura_car_id = p_aura_car_id
        AND g.aura_owner_user_id = p_aura_owner_user_id
        AND g.published = true
        AND g.revoked_at IS NULL
    )
    AND NOT EXISTS (
      SELECT 1 FROM public.aura_billing_published_documents a
      WHERE a.document_id = d.id
    )
  RETURNING d.id, d.doc_number INTO v_id, v_number;

  IF v_id IS NULL THEN
    RETURN false;
  END IF;

  INSERT INTO public.billing_activity_log(
    entity_type, entity_id, action, metadata
  ) VALUES (
    'document', v_id, 'aura_advisor_issued_native_estimate',
    jsonb_build_object(
      'source', 'Aura authenticated administrator, signed Billing bridge',
      'aura_advisor_user_id', p_aura_advisor_user_id,
      'aura_owner_user_id', p_aura_owner_user_id,
      'aura_car_id', p_aura_car_id,
      'billing_document_number', v_number,
      'billing_job_id', p_job_id,
      'revision', p_expected_revision,
      'confirmed_total', p_expected_total,
      'previous_status', 'draft', 'current_status', 'issued',
      'owner_publication', false, 'client_email_sent', false,
      'payment_recorded', false
    )
  );
  RETURN true;
END;
$function$;

REVOKE ALL ON FUNCTION public.aura_issue_billing_estimate(
  uuid, uuid, uuid, uuid, integer, integer, integer, integer, numeric, timestamptz
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.aura_issue_billing_estimate(
  uuid, uuid, uuid, uuid, integer, integer, integer, integer, numeric, timestamptz
) TO service_role;

COMMENT ON FUNCTION public.aura_issue_billing_estimate(
  uuid, uuid, uuid, uuid, integer, integer, integer, integer, numeric, timestamptz
) IS 'Atomically issue a current native draft explicitly confirmed by an Aura administrator through the signed, nonce-protected Billing bridge. No publication or delivery.';
