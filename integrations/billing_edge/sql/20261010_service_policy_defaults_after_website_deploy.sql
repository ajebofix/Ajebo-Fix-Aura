-- Apply only after https://ajebofix.com/service-terms is live on Cloudflare
-- and Ajebo Fix approves the published policy wording.
-- Affects future NEW documents via billing_settings; does not edit any
-- existing signed estimate, invoice, receipt or payment.
begin;
update public.billing_settings
set
  default_estimate_terms = case
    when coalesce(default_estimate_terms,'') like '%https://ajebofix.com/service-terms%'
      then default_estimate_terms
    else concat_ws(E'\n',nullif(default_estimate_terms,''),
      'The estimate covers the approved scope only. Additional paid work requires separate approval. General service policies: https://ajebofix.com/service-terms. Specific accepted job terms take precedence.')
  end,
  default_invoice_terms = case
    when coalesce(default_invoice_terms,'') like '%https://ajebofix.com/service-terms%'
      then default_invoice_terms
    else concat_ws(E'\n',nullif(default_invoice_terms,''),
      'Recorded partial payments reduce this same invoice balance. A payment receipt is separate and does not confirm final settlement. General service policies: https://ajebofix.com/service-terms. Specific accepted job terms take precedence.')
  end,
  default_receipt_note = case
    when coalesce(default_receipt_note,'') like '%https://ajebofix.com/service-terms%'
      then default_receipt_note
    else concat_ws(E'\n',nullif(default_receipt_note,''),
      'An individual receipt acknowledges only its stated recorded payment. A consolidated final receipt confirms settlement only after the original invoice balance is fully reconciled; it does not create a new payment. Policies: https://ajebofix.com/service-terms.')
  end,
  estimate_disclaimer = case
    when coalesce(estimate_disclaimer,'') like '%https://ajebofix.com/service-terms%'
      then estimate_disclaimer
    else concat_ws(E'\n',nullif(estimate_disclaimer,''),
      'Read general service policies: https://ajebofix.com/service-terms. Written approved scope, timing and price govern this estimate.')
  end,
  invoice_disclaimer = case
    when coalesce(invoice_disclaimer,'') like '%https://ajebofix.com/service-terms%'
      then invoice_disclaimer
    else concat_ws(E'\n',nullif(invoice_disclaimer,''),
      'An outstanding balance remains payable under the accepted agreement even after a partial payment. Policies: https://ajebofix.com/service-terms.')
  end,
  receipt_disclaimer = case
    when coalesce(receipt_disclaimer,'') like '%https://ajebofix.com/service-terms%'
      then receipt_disclaimer
    else concat_ws(E'\n',nullif(receipt_disclaimer,''),
      'This is not proof of complete settlement unless it is explicitly an authorised consolidated final receipt with a verified zero invoice balance. Policies: https://ajebofix.com/service-terms.')
  end,
  updated_at = now()
where id = true;
commit;
