/**
 * Прокси для userbet.info (обход CORS в iOS web).
 * Deploy: supabase functions deploy userbet-odds
 */
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const USERBET_URL = "https://userbet.info/user/get_current_lineups_odds/";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: cors });
  }
  try {
    const payload = await req.json();
    const idFixture = String(payload?.id_fixture ?? "").trim();
    if (!idFixture) {
      return new Response(JSON.stringify({ error: "id_fixture required" }), {
        status: 400,
        headers: { ...cors, "Content-Type": "application/json" },
      });
    }
    const body = new URLSearchParams({ id_fixture: idFixture });
    const upstream = await fetch(USERBET_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        Accept: "application/json, text/html, */*",
      },
      body: body.toString(),
    });
    const text = await upstream.text();
    if (!upstream.ok) {
      return new Response(JSON.stringify({ error: "upstream failed" }), {
        status: 502,
        headers: { ...cors, "Content-Type": "application/json" },
      });
    }
    return new Response(text, {
      status: 200,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  } catch (_e) {
    return new Response(JSON.stringify({ error: "proxy error" }), {
      status: 500,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }
});
