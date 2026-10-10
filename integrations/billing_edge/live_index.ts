// Ajebo Fix Billing → Aura: purpose-derived Ed25519 signed, read-only projection.
// Verify Aura signatures using its published key, with timestamp and nonce replay controls.
// The Billing service-role key never leaves the Supabase function environment.
const PROJECT_URL = Deno.env.get("SUPABASE_URL") || "";
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
const normaliseVin = (v: unknown) => String(v ?? "").toUpperCase().replace(/[^A-Z0-9]/g, "");
const uuid = (v: unknown) => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const respond = (status: number, body: object) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});
const empty = (state: string) => ({ state, documents: [], payments: [] });

async function fromBilling(table: string, query: string): Promise<Record<string, unknown>[]> {
  const allow = new Set([
    "aura_billing_bridge_credentials", "aura_billing_publication_grants",
    "aura_billing_published_documents", "billing_vehicles",
    "billing_documents", "billing_payments", "billing_settings",
    "billing_clients", "billing_jobs",
  ]);
  if (!allow.has(table) || !PROJECT_URL || !SERVICE_KEY) throw new Error("Bridge unavailable");
  const r = await fetch(`${PROJECT_URL}/rest/v1/${table}?${query}`, {
    method: "GET", headers: { "apikey": SERVICE_KEY, "authorization": `Bearer ${SERVICE_KEY}`, "accept": "application/json" },
    signal: AbortSignal.timeout(7000),
  });
  if (!r.ok) throw new Error("Bridge read error");
  const data = await r.json();
  if (!Array.isArray(data) || data.length > 100 || data.some((v) => !v || typeof v !== "object" || Array.isArray(v))) throw new Error("Bridge result invalid");
  return data;
}

const AURA_PUBLIC_KEY_URL =
  "https://ajebo-fix-aura-production-e7c6.up.railway.app/.well-known/aura-billing-public-key";
const decodeUrl = (encoded: string) => {
  if (!/^[A-Za-z0-9_-]+$/.test(encoded)) throw new Error("Invalid signature encoding");
  const padded = encoded.replace(/-/g, "+").replace(/_/g, "/") +
    "=".repeat((4 - encoded.length % 4) % 4);
  return Uint8Array.from(atob(padded), c => c.charCodeAt(0));
};
async function signedRequestValid(req: Request, body: string): Promise<boolean> {
  const kid = req.headers.get("x-aura-key-id");
  const stamp = req.headers.get("x-aura-timestamp") || "";
  const nonce = req.headers.get("x-aura-nonce") || "";
  const sig = req.headers.get("x-aura-signature") || "";
  if (kid !== "aura-billing-v1" ||
      !/^[0-9]{10}$/.test(stamp) || !/^[a-f0-9]{32}$/.test(nonce) ||
      !/^[A-Za-z0-9_-]{86}$/.test(sig)) return false;
  const now = Math.floor(Date.now()/1000);
  if (Math.abs(now - Number(stamp)) > 90) return false;

  const response = await fetch(AURA_PUBLIC_KEY_URL, {
    method: "GET", redirect: "error", signal: AbortSignal.timeout(5500),
    headers: { "accept": "application/json" },
  });
  if (!response.ok) throw new Error("Public key verification unavailable");
  const jwk = await response.json();
  if (jwk?.kty !== "OKP" || jwk?.crv !== "Ed25519" ||
      jwk?.kid !== kid || typeof jwk?.x !== "string") return false;
  const pubBytes = decodeUrl(jwk.x);
  if (pubBytes.length !== 32) return false;
  const publicKey = await crypto.subtle.importKey(
    "raw", pubBytes, { name: "Ed25519" }, false, ["verify"],
  );
  const message = new TextEncoder().encode(stamp + "." + nonce + "." + body);
  if (!await crypto.subtle.verify(
    { name: "Ed25519" }, publicKey, decodeUrl(sig), message,
  )) return false;

  // Only *verified* requests reserve their nonce. Replays cannot disclose
  // commercial records even if a signed request were captured.
  const replay = await fetch(
    `${PROJECT_URL}/rest/v1/aura_billing_bridge_nonces`, {
      method: "POST",
      headers: {
        "apikey": SERVICE_KEY,
        "authorization": `Bearer ${SERVICE_KEY}`,
        "content-type": "application/json",
        "prefer": "return=minimal",
      },
      body: JSON.stringify({
        nonce, expires_at: new Date((Number(stamp) + 180)*1000).toISOString(),
      }),
      signal: AbortSignal.timeout(5000),
    },
  );
  if (replay.status === 409) return false;
  if (replay.status !== 201) throw new Error("Replay persistence unavailable");
  return true;
}
function money(n: unknown): number {
  const value = Number(n ?? 0);
  if (!Number.isFinite(value) || value < 0) throw new Error("Invalid monetary value");
  return value;
}
Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") return respond(405, { error: "Method not allowed" });
  try {
    if (Number(req.headers.get("content-length") || 0) > 2048) return respond(413, { error: "Payload too large" });
    const rawBody = await req.text();
    if (rawBody.length > 2048) return respond(413, { error: "Payload too large" });
    if (!(await signedRequestValid(req, rawBody))) return respond(401, { error: "Unauthorized" });
    const payload = JSON.parse(rawBody);
    const carId = payload?.car_id;
    const ownerUserId = payload?.owner_user_id;
    const vin = normaliseVin(payload?.vin);
    if (!Number.isSafeInteger(carId) || carId < 1 || !Number.isSafeInteger(ownerUserId) || ownerUserId < 1 || vin.length !== 17) {
      return respond(400, { error: "Invalid request" });
    }

    const isAdvisorPreview = payload?.action === "advisor_preview";
    const isAdvisorInventory = payload?.action === "advisor_documents";
    const isAdvisorIssue = payload?.action === "issue_document";
    if ((isAdvisorPreview || isAdvisorInventory || isAdvisorIssue) && (
      !Number.isSafeInteger(payload?.advisor_user_id) ||
      payload.advisor_user_id < 1
    )) return respond(400, { error: "Invalid advisor request" });
    const grants = await fromBilling("aura_billing_publication_grants",
      `select=billing_vehicle_id,billing_client_id&revoked_at=is.null&aura_car_id=eq.${carId}&aura_owner_user_id=eq.${ownerUserId}${(isAdvisorPreview || isAdvisorInventory || isAdvisorIssue) ? "" : "&published=eq.true"}&limit=2`);
    if (grants.length !== 1) return respond(200, empty("not_published"));
    const { billing_vehicle_id: vehicleId, billing_client_id: clientId } = grants[0];
    if (!uuid(vehicleId) || !uuid(clientId)) throw new Error("Invalid link");
    const vehicles = await fromBilling("billing_vehicles",
      `select=id,client_id,vin,aura_vehicle_ref&id=eq.${vehicleId}&limit=2`);
    if (vehicles.length !== 1 || vehicles[0].client_id !== clientId ||
        String(vehicles[0].aura_vehicle_ref || "") !== String(carId) ||
        normaliseVin(vehicles[0].vin) !== vin) return respond(200, empty("not_linked"));

    if (isAdvisorInventory) {
      // Only authenticated advisors can request this signed inventory.
      // The same grant + exact full VIN + active owner binding has been checked.
      const rows = await fromBilling("billing_documents",
        `select=id,doc_type,doc_number,status,issue_date,currency_symbol,total,job_id,created_by,superseded_at,share_revoked_at&client_id=eq.${clientId}&vehicle_id=eq.${vehicleId}&order=created_at.desc&limit=75`);
      const releases = await fromBilling("aura_billing_published_documents",
        `select=document_id&aura_car_id=eq.${carId}&revoked_at=is.null&limit=100`);
      const published = new Set(releases.map(p => p.document_id).filter(uuid));
      const documents: object[] = [];
      for (const d of rows) {
        if (!uuid(d.id) || d.superseded_at || d.share_revoked_at) continue;
        const kind = String(d.doc_type || "").toLowerCase();
        if (!["estimate","invoice","receipt"].includes(kind)) continue;
        const state = String(d.status || "").toLowerCase();
        if (!["draft","issued","sent","accepted","paid","partially_paid","overdue"].includes(state)) continue;
        documents.push({
          id:d.id, kind, number:String(d.doc_number || "").slice(0,80),
          status:state, issued:String(d.issue_date || "").slice(0,10),
          amount:String(money(d.total)), currency:String(d.currency_symbol || "₦").slice(0,5),
          native_created:uuid(d.created_by), job_linked:uuid(d.job_id),
          published:published.has(d.id),
        });
      }
      // Each new recorded transfer is distinct from the invoice's previous
      // email submission. Advisor-only payment inventory allows the UI to
      // explain which individual receipts still need to be created in Billing.
      // No customer-facing transaction identifiers, private notes or bank
      // details are exposed, and this action is strictly read-only.
      const invoiceIds = rows.filter(d =>
        d.doc_type === "invoice" && uuid(d.id) && uuid(d.job_id) &&
        !d.superseded_at && !d.share_revoked_at
      ).map(d => d.id as string);
      const payments: object[] = [];
      if (invoiceIds.length) {
        const allowedInvoices = new Set(invoiceIds);
        const sourcePayments = await fromBilling("billing_payments",
          `select=id,invoice_id,amount,paid_at,receipt_document_id&invoice_id=in.(${invoiceIds.join(",")})&order=paid_at.desc&limit=100`);
        for (const p of sourcePayments) {
          if (!uuid(p.id) || !allowedInvoices.has(p.invoice_id) ||
              (p.receipt_document_id !== null && !uuid(p.receipt_document_id))) {
            throw new Error("Invalid advisor payment reference");
          }
          payments.push({
            id:p.id,invoice_id:p.invoice_id,
            amount:String(money(p.amount)),
            paid_at:String(p.paid_at||"").slice(0,10),
            has_receipt:uuid(p.receipt_document_id),
            receipt_document_id:uuid(p.receipt_document_id) ? p.receipt_document_id : null,
          });
        }
      }
      return respond(200, {state:"linked",documents,payments});
    }

    if (payload?.action === "issue_document") {
      // Advisor-approved, independent action: issue the existing native Billing
      // draft without publishing it or sending a message. Authenticated Aura
      // admin action + Ed25519 signature + nonce + exact owner/VIN linkage are
      // already verified above. Client routes cannot invoke this action.
      const advisorId = payload?.advisor_user_id;
      const targetId = payload?.document_id;
      const expectedRevision = payload?.expected_revision;
      const expectedTotal = String(payload?.expected_total ?? "");
      const expectedUpdatedAt = String(payload?.expected_updated_at ?? "");
      if (!Number.isSafeInteger(advisorId) || advisorId < 1 || !uuid(targetId) ||
          !Number.isSafeInteger(expectedRevision) || expectedRevision < 1 ||
          !/^[0-9]{1,12}(?:\.[0-9]{1,2})?$/.test(expectedTotal) ||
          !/^20[0-9]{2}-[0-9]{2}-[0-9]{2}T/.test(expectedUpdatedAt)) {
        return respond(400, { error: "Invalid issue confirmation" });
      }
      const candidates = await fromBilling("billing_documents",
        `select=id,doc_type,doc_number,status,total,terms,revision_no,created_by,job_id,client_id,vehicle_id,superseded_at,share_revoked_at,valid_until,updated_at&id=eq.${targetId}&client_id=eq.${clientId}&vehicle_id=eq.${vehicleId}&limit=2`);
      if (candidates.length !== 1) return respond(404, {error:"Estimate not found"});
      const d = candidates[0];
      if (d.doc_type !== "estimate" || d.status !== "draft" ||
          !uuid(d.created_by) || !uuid(d.job_id) || d.superseded_at ||
          d.share_revoked_at || money(d.total) <= 0 ||
          Number(d.revision_no) !== expectedRevision ||
          money(d.total) !== Number(expectedTotal) ||
          d.updated_at !== expectedUpdatedAt || !String(d.terms || "").trim()) {
        return respond(409, {error:"Native draft needs another review"});
      }
      const job = await fromBilling("billing_jobs",
        `select=id,job_number,client_id,vehicle_id&id=eq.${d.job_id}&client_id=eq.${clientId}&vehicle_id=eq.${vehicleId}&limit=2`);
      if (job.length !== 1) return respond(409, {error:"Job identity mismatch"});
      // Pilot-specific agreed payment amounts must be printed on THIS revision.
      if (job[0].job_number === "JOB-2026-003") {
        const amount = money(d.total);
        const fmt = (n: number) => "₦" + n.toLocaleString("en-US", {
          maximumFractionDigits:2,
        });
        const terms = String(d.terms || "");
        if (!terms.includes("90%") || !terms.includes(fmt(amount*0.9)) ||
            !terms.includes(fmt(amount*0.1))) {
          return respond(409, {error:"Current revision payment terms need correction"});
        }
      }
      const released = await fromBilling("aura_billing_published_documents",
        `select=document_id&document_id=eq.${targetId}&limit=1`);
      if (released.length) return respond(409, {error:"Draft publication conflict"});
      // One service-only SQL transaction enforces the final status, amount,
      // revision, document freshness, job version and full audit record.
      const issued = await fetch(`${PROJECT_URL}/rest/v1/rpc/aura_issue_billing_estimate`, {
        method:"POST",
        headers:{
          apikey:SERVICE_KEY,
          authorization:`Bearer ${SERVICE_KEY}`,
          "content-type":"application/json",
        },
        body:JSON.stringify({
          p_document_id:targetId,
          p_client_id:clientId,
          p_vehicle_id:vehicleId,
          p_job_id:String(d.job_id),
          p_aura_car_id:carId,
          p_aura_owner_user_id:ownerUserId,
          p_aura_advisor_user_id:advisorId,
          p_expected_revision:expectedRevision,
          p_expected_total:Number(expectedTotal),
          p_expected_updated_at:expectedUpdatedAt,
        }),
        signal:AbortSignal.timeout(7000),
      });
      if (!issued.ok) throw new Error("Issue transaction unavailable");
      if (await issued.json() !== true) {
        return respond(409,{error:"Estimate changed during approval"});
      }
      return respond(200,{state:"issued",document_id:targetId});
    }

    if (payload?.action === "publish_document") {
      const advisorId = payload?.advisor_user_id;
      const targetId = payload?.document_id;
      if (!Number.isSafeInteger(advisorId) || advisorId < 1 || !uuid(targetId)) {
        return respond(400, { error: "Invalid advisor publication" });
      }
      // The caller is Aura's authenticated advisor route. The Ed25519 signed
      // payload is body-bound and nonce/replay guarded; no client can forge it.
      const candidates = await fromBilling("billing_documents",
        `select=id,doc_type,status,created_by,job_id,client_id,vehicle_id,superseded_at,share_revoked_at,total,source_document_id,receipt_kind&id=eq.${targetId}&client_id=eq.${clientId}&vehicle_id=eq.${vehicleId}&limit=2`);
      if (candidates.length !== 1) return respond(404, { error: "Document not found" });
      const d = candidates[0];
      // Release only native, independently sourced and eligible documents.
      // Publishing does not email, acknowledge payment, or create a receipt.
      const eligible: Record<string, string[]> = {
        estimate:["issued","sent"],
        invoice:["issued","sent","overdue","partially_paid","paid"],
        receipt:["issued"],
      };
      const kind = String(d.doc_type || "");
      if (!eligible[kind]?.includes(String(d.status)) ||
          !uuid(d.created_by) || !uuid(d.job_id) ||
          d.superseded_at || d.share_revoked_at || money(d.total) <= 0) {
        return respond(409, {error:"Native document not eligible for publication"});
      }
      const job = await fromBilling("billing_jobs",
        `select=id,client_id,vehicle_id&id=eq.${d.job_id}&client_id=eq.${clientId}&vehicle_id=eq.${vehicleId}&limit=2`);
      if (job.length !== 1) return respond(409, {error:"Job identity mismatch"});
      if (kind === "receipt") {
        // A receipt must reflect an existing payment against a previously
        // published native invoice; it cannot invent a payment.
        if (!uuid(d.source_document_id)) return respond(409,{error:"Receipt lacks source invoice"});
        const invoices = await fromBilling("billing_documents",
          `select=id,doc_type,job_id,client_id,vehicle_id,status&id=eq.${d.source_document_id}&client_id=eq.${clientId}&vehicle_id=eq.${vehicleId}&limit=2`);
        if (invoices.length !== 1 || invoices[0].doc_type !== "invoice" ||
            invoices[0].job_id !== d.job_id) {
          return respond(409,{error:"Receipt source invoice mismatch"});
        }
        const invoicePublished = await fromBilling("aura_billing_published_documents",
          `select=document_id&document_id=eq.${d.source_document_id}&aura_car_id=eq.${carId}&revoked_at=is.null&limit=1`);
        if (invoicePublished.length !== 1) return respond(409,{error:"Publish source invoice first"});
        const payColumn = d.receipt_kind === "consolidated"
          ? "consolidated_receipt_id" : d.receipt_kind === "payment"
          ? "receipt_document_id" : null;
        if (!payColumn) return respond(409,{error:"Receipt payment type unverified"});
        const paymentRows = await fromBilling("billing_payments",
          `select=id,amount&invoice_id=eq.${d.source_document_id}&${payColumn}=eq.${targetId}&limit=100`);
        if (!paymentRows.length) return respond(409,{error:"Receipt has no recorded payment"});
        const linkedTotal = paymentRows.reduce((sum,p) => sum + money(p.amount), 0);
        if (Math.abs(linkedTotal - money(d.total)) > 0.01) {
          return respond(409,{error:"Receipt amount differs from paid amount"});
        }
      }
      const already = await fromBilling("aura_billing_published_documents",
        `select=document_id,revoked_at,aura_car_id&document_id=eq.${targetId}&limit=2`);
      if (already.length > 0) {
        if (already.length === 1 && already[0].aura_car_id === carId &&
            already[0].revoked_at === null) {
          return respond(200, { state: "already_published" });
        }
        return respond(409, { error: "Existing or revoked publication requires separate review" });
      }
      const publish = await fetch(
        `${PROJECT_URL}/rest/v1/aura_billing_published_documents`, {
        method: "POST",
        headers: {
          apikey: SERVICE_KEY,
          authorization: `Bearer ${SERVICE_KEY}`,
          "content-type": "application/json",
          prefer: "return=minimal",
        },
        body: JSON.stringify({
          aura_car_id: carId,
          document_id: targetId,
          approved_by: `Aura authorised advisor ${advisorId}`,
        }),
        signal: AbortSignal.timeout(7000),
      });
      if (publish.status === 409) return respond(409, { error: "Publication conflict" });
      if (publish.status !== 201) throw new Error("Publication persistence failed");
      return respond(200, { state: "published" });
    }

    const releases = await fromBilling("aura_billing_published_documents",
      `select=document_id&aura_car_id=eq.${carId}&revoked_at=is.null&limit=100`);
    const documentIds = releases.map((r) => r.document_id).filter(uuid) as string[];
    if (documentIds.length !== releases.length) throw new Error("Invalid released document");
    const action = isAdvisorPreview ? "advisor_preview" :
      payload?.action === "document" ? "document" : "list";
    if (action === "document" || action === "advisor_preview") {
      const targetId = payload?.document_id;
      if (!uuid(targetId) || (action === "document" && !documentIds.includes(targetId))) {
        return respond(404, { error: "Document not available" });
      }
      const detail = await fromBilling("billing_documents",
        `select=id,doc_type,doc_number,status,issue_date,due_date,valid_until,currency_symbol,total,amount_paid,gross_subtotal,adjustment_total,vat_amount,vehicle_id,client_id,job_id,scope,sections,terms,revision_no,superseded_at,share_revoked_at,updated_at&id=eq.${targetId}&vehicle_id=eq.${vehicleId}&client_id=eq.${clientId}&limit=2`);
      if (detail.length !== 1) return respond(404, { error: "Document not available" });
      const d = detail[0];
      const kind = String(d.doc_type || "").toLowerCase();
      const state = String(d.status || "").toLowerCase();
      const allowed: Record<string,string[]> = {
        estimate:["accepted","issued","sent"],
        invoice:["issued","sent","overdue","partially_paid","paid"],
        receipt:["issued"],
      };
      if (d.superseded_at || d.share_revoked_at ||
          !(action === "advisor_preview" && kind === "estimate" && state === "draft")
          && !allowed[kind]?.includes(state)) {
        return respond(404, { error: "Document not available" });
      }
      const sections: object[] = [];
      // Source discount rows are distinct from charge rows. Never project
      // adjustment.note, which may contain confidential advisor negotiation.
      const discountRows: {description:string,amount:string}[] = [];
      const rawSections = Array.isArray(d.sections) ? d.sections : [];
      if (rawSections.length > 25) throw new Error("Too many sections");
      for (const section of rawSections) {
        if (!section || typeof section !== "object") throw new Error("Invalid section");
        const rows: object[] = [];
        const sourceRows = Array.isArray(section.rows) ? section.rows : [];
        if (sourceRows.length > 100) throw new Error("Too many rows");
        for (const item of sourceRows) {
          if (!item || typeof item !== "object") continue;
          if (item.kind === "adjustment") {
            const signedAmount = Number(item.amount);
            if (!Number.isFinite(signedAmount)) throw new Error("Invalid Billing adjustment");
            if (item.adjustment_type === "discount" && signedAmount < 0) {
              if (discountRows.length >= 25) throw new Error("Too many discounts");
              discountRows.push({
                description:String(item.description||"Discount").slice(0,140),
                amount:String(-signedAmount),
              });
            }
            continue;
          }
          if (item.kind !== "line") continue;
          rows.push({
            description:String(item.description||"").slice(0,220),
            quantity:String(money(item.qty ?? 1)),
            unit_price:String(money(item.unit_price)),
            amount:String(money(item.amount)),
          });
        }
        sections.push({title:String(section.title||"").slice(0,160),rows});
      }
      // Present a discount only when the authentic Billing totals reconcile.
      // Never compute a new price or assume an owner's requested settlement.
      let discountBreakdown: {gross_subtotal:string,discounts:object[]} | null = null;
      if (discountRows.length && kind !== "receipt") {
        const gross = money(d.gross_subtotal);
        const discountSum = discountRows.reduce((sum,row) => sum + Number(row.amount), 0);
        const adjustmentsTotal = money(d.adjustment_total);
        const vat = money(d.vat_amount);
        if (gross <= 0 || Math.abs(discountSum-adjustmentsTotal)>0.011 ||
            Math.abs(gross-discountSum+vat-money(d.total))>0.011) {
          throw new Error("Source discount and invoice amount do not reconcile");
        }
        discountBreakdown = {gross_subtotal:String(gross), discounts:discountRows};
      }
      const settings = await fromBilling("billing_settings",
        "select=business_name,tagline,logo_url,website,primary_color,secondary_color,accent_color,footer_strap&id=eq.true&limit=1");
      if (settings.length !== 1) throw new Error("Billing branding unavailable");
      const brand = settings[0];
      const clients = await fromBilling("billing_clients",
        `select=id,name&id=eq.${clientId}&limit=1`);
      if (clients.length !== 1 || clients[0].id !== clientId) {
        throw new Error("Unverified Billing client");
      }
      const jobs = d.job_id && uuid(d.job_id) ? await fromBilling("billing_jobs",
        `select=id,job_number,sow_number,client_id,vehicle_id&id=eq.${d.job_id}&client_id=eq.${clientId}&vehicle_id=eq.${vehicleId}&limit=1`) : [];
      if (d.job_id && jobs.length !== 1) throw new Error("Unverified Billing job");
      const document = {
        id:d.id, kind, group:d.job_id ? "job_record" : "vehicle_history",
        number:String(d.doc_number||"").slice(0,80), status:state,
        billed_to:String(clients[0].name||"").slice(0,120),
        vin:normaliseVin(vehicles[0].vin).slice(0,17),
        job_number:jobs.length ? String(jobs[0].job_number||"").slice(0,80) : "",
        sow_number:jobs.length ? String(jobs[0].sow_number||"").slice(0,80) : "",
        issued:String(d.issue_date||"").slice(0,10),
        due:String(d.due_date||"").slice(0,10),
        valid_until:String(d.valid_until||"").slice(0,10),
        currency:String(d.currency_symbol||"₦").slice(0,5),
        total:String(money(d.total)), paid:String(money(d.amount_paid)),
        balance:String(Math.max(0,money(d.total)-money(d.amount_paid))),
        ...(discountBreakdown || {}),
        scope:String(d.scope||"").slice(0,1400),
        terms:String(d.terms||"").slice(0,2400),
        revision:Number(d.revision_no||1),
        updated_at:action === "advisor_preview" ? String(d.updated_at||"").slice(0,60) : "",
        sections,
      };
      return respond(200,{
        state:"linked", preview:isAdvisorPreview, document,
        brand:{
          name:String(brand.business_name||"Ajebo Fix Ltd").slice(0,100),
          tagline:String(brand.tagline||"").slice(0,140),
          logo:String(brand.logo_url||"").slice(0,600),
          website:String(brand.website||"").slice(0,150),
          footer:String(brand.footer_strap||"").slice(0,160),
          primary:String(brand.primary_color||"#0a1628").slice(0,20),
          secondary:String(brand.secondary_color||"#142236").slice(0,20),
          accent:String(brand.accent_color||"#d4a017").slice(0,20),
        }
      });
    }
    if (!documentIds.length) return respond(200, empty("linked"));

    const rows = await fromBilling("billing_documents",
      `select=id,doc_type,doc_number,status,issue_date,due_date,currency_symbol,total,amount_paid,vehicle_id,client_id,job_id,superseded_at,share_revoked_at&id=in.(${documentIds.join(",")})&vehicle_id=eq.${vehicleId}&client_id=eq.${clientId}&limit=100`);
    const released = new Set(documentIds);
    const states: Record<string, string[]> = {
      estimate: ["accepted", "issued", "sent"],
      invoice: ["issued", "sent", "overdue", "partially_paid", "paid"],
      receipt: ["issued"],
    };
    const documents: object[] = [], invoiceIds: string[] = [];
    for (const row of rows) {
      const kind = String(row.doc_type || "").toLowerCase();
      const status = String(row.status || "").toLowerCase();
      if (!uuid(row.id) || !released.has(row.id as string) || !states[kind]?.includes(status) ||
          row.superseded_at || row.share_revoked_at) continue;
      const total = money(row.total);
      const paid = kind === "invoice" ? money(row.amount_paid) : 0;
      documents.push({
        id:row.id, kind, group: row.job_id ? "job_record" : "vehicle_history",
        number: String(row.doc_number || "").slice(0, 80), status,
        issued: String(row.issue_date || "").slice(0, 10),
        due: String(row.due_date || "").slice(0, 10),
        currency: String(row.currency_symbol || "₦").slice(0, 5),
        total: String(total), paid: String(paid), balance: String(Math.max(0, total - paid)),
      });
      if (kind === "invoice") invoiceIds.push(row.id as string);
    }
    const payments: object[] = [];
    if (invoiceIds.length) {
      const invoiceSet = new Set(invoiceIds);
      const paymentRows = await fromBilling("billing_payments",
        `select=id,invoice_id,amount,paid_at,currency_symbol&invoice_id=in.(${invoiceIds.join(",")})&limit=100`);
      for (const p of paymentRows) {
        if (!uuid(p.id) || !invoiceSet.has(String(p.invoice_id || ""))) continue;
        payments.push({ amount: String(money(p.amount)), currency: String(p.currency_symbol || "₦").slice(0,5),
          paid_at: String(p.paid_at || "").slice(0,10) });
      }
    }
    return respond(200, { state: "linked", documents, payments });
  } catch (_) {
    return respond(503, { error: "Billing projection unavailable" });
  }
});
