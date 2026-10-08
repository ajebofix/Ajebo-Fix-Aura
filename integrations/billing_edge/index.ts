// Ajebo Fix Billing → Aura: token-protected, read-only, published projection.
// This function authenticates every request before any account lookup.
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
    "billing_documents", "billing_payments",
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
async function tokenIsValid(bearer: string | null): Promise<boolean> {
  if (!bearer || !/^Bearer [a-f0-9]{64}$/.test(bearer)) return false;
  const tokens = await fromBilling("aura_billing_bridge_credentials", "select=token_sha256&name=eq.primary&active=eq.true&limit=1");
  if (tokens.length !== 1) return false;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(bearer.slice(7)));
  const hex = Array.from(new Uint8Array(digest), (v) => v.toString(16).padStart(2, "0")).join("");
  const expected = String(tokens[0].token_sha256 || "");
  let difference = hex.length ^ expected.length;
  for (let i = 0; i < hex.length; i++) difference |= hex.charCodeAt(i) ^ (expected.charCodeAt(i) || 0);
  return difference === 0;
}
function money(n: unknown): number {
  const value = Number(n ?? 0);
  if (!Number.isFinite(value) || value < 0) throw new Error("Invalid monetary value");
  return value;
}
Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") return respond(405, { error: "Method not allowed" });
  try {
    if (!(await tokenIsValid(req.headers.get("authorization")))) return respond(401, { error: "Unauthorized" });
    if (Number(req.headers.get("content-length") || 0) > 2048) return respond(413, { error: "Payload too large" });
    const payload = await req.json();
    const carId = payload?.car_id;
    const ownerUserId = payload?.owner_user_id;
    const vin = normaliseVin(payload?.vin);
    if (!Number.isSafeInteger(carId) || carId < 1 || !Number.isSafeInteger(ownerUserId) || ownerUserId < 1 || vin.length !== 17) {
      return respond(400, { error: "Invalid request" });
    }

    const grants = await fromBilling("aura_billing_publication_grants",
      `select=billing_vehicle_id,billing_client_id&published=eq.true&revoked_at=is.null&aura_car_id=eq.${carId}&aura_owner_user_id=eq.${ownerUserId}&limit=2`);
    if (grants.length !== 1) return respond(200, empty("not_published"));
    const { billing_vehicle_id: vehicleId, billing_client_id: clientId } = grants[0];
    if (!uuid(vehicleId) || !uuid(clientId)) throw new Error("Invalid link");
    const vehicles = await fromBilling("billing_vehicles",
      `select=id,client_id,vin,aura_vehicle_ref&id=eq.${vehicleId}&limit=2`);
    if (vehicles.length !== 1 || vehicles[0].client_id !== clientId ||
        String(vehicles[0].aura_vehicle_ref || "") !== String(carId) ||
        normaliseVin(vehicles[0].vin) !== vin) return respond(200, empty("not_linked"));

    const releases = await fromBilling("aura_billing_published_documents",
      `select=document_id&aura_car_id=eq.${carId}&revoked_at=is.null&limit=100`);
    const documentIds = releases.map((r) => r.document_id).filter(uuid) as string[];
    if (documentIds.length !== releases.length) throw new Error("Invalid released document");
    if (!documentIds.length) return respond(200, empty("linked"));

    const rows = await fromBilling("billing_documents",
      `select=id,doc_type,doc_number,status,issue_date,due_date,currency_symbol,total,amount_paid,vehicle_id,client_id,superseded_at,share_revoked_at&id=in.(${documentIds.join(",")})&vehicle_id=eq.${vehicleId}&client_id=eq.${clientId}&limit=100`);
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
        kind, number: String(row.doc_number || "").slice(0, 80), status,
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
