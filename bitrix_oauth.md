// supabase/functions/bitrix-oauth/index.ts
import { serve } from "https://deno.land/std@0.177.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, GET, OPTIONS'
};
const BITRIX_CLIENT_ID = Deno.env.get("BITRIX_CLIENT_ID");
const BITRIX_CLIENT_SECRET = Deno.env.get("BITRIX_CLIENT_SECRET");
const FUNCTION_REDIRECT_URI = `${Deno.env.get("SUPABASE_URL")}/functions/v1/bitrix-oauth`;
const FINAL_SUCCESS_REDIRECT_URI = `${Deno.env.get("SUPABASE_URL")}/static/bitrix-auth-success.html`;
const FINAL_ERROR_REDIRECT_URI = `${Deno.env.get("SUPABASE_URL")}/static/bitrix-auth-error.html`;
async function saveTokens(supabaseClient, userId, portalUrl, tokens) {
  console.log("[SAVE TOKENS] Attempting to save tokens. Supabase User ID:", userId, "Portal:", portalUrl);
  // Осторожно логируем токены, особенно access_token и refresh_token
  const partialTokensForLog = {
    ...tokens
  };
  if (partialTokensForLog.access_token) partialTokensForLog.access_token = partialTokensForLog.access_token.substring(0, 20) + "...";
  if (partialTokensForLog.refresh_token) partialTokensForLog.refresh_token = partialTokensForLog.refresh_token.substring(0, 20) + "...";
  console.log("[SAVE TOKENS] Tokens object received (partially logged):", JSON.stringify(partialTokensForLog));
  const expiresAt = new Date(Date.now() + tokens.expires_in * 1000);
  console.log("[SAVE TOKENS] Calculated expires_at:", expiresAt.toISOString());
  const dataToUpsert = {
    user_id: userId,
    portal_url: portalUrl,
    bitrix_user_id: tokens.user_id ? tokens.user_id.toString() : null,
    member_id: tokens.member_id,
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    expires_at: expiresAt.toISOString()
  };
  console.log("[SAVE TOKENS] Data to upsert into 'bitrix_integrations':", JSON.stringify(dataToUpsert));
  try {
    const { error, data } = await supabaseClient.from("bitrix_integrations").upsert(dataToUpsert, {
      onConflict: "user_id, portal_url" // Убедитесь, что такой constraint существует и корректен
    });
    if (error) {
      console.error("[SAVE TOKENS] Supabase upsert error object:", JSON.stringify(error, null, 2));
      throw error;
    }
    console.log("[SAVE TOKENS] Bitrix24 tokens saved/updated successfully in 'bitrix_integrations'. Upsert result data:", data);
  } catch (e) {
    console.error("[SAVE TOKENS] Exception during upsert operation:", e.message, e.stack, JSON.stringify(e, null, 2));
    throw new Error(`Failed in saveTokens: ${e.message || JSON.stringify(e)}`);
  }
}
serve(async (req)=>{
  console.log(`[REQUEST RECEIVED] Method: ${req.method}, URL: ${req.url}`);
 if (req.method === "OPTIONS") {
  console.log("[OPTIONS HANDLER] Responding to OPTIONS request.");
  return new Response("ok", {
    headers: corsHeaders
  });
}

if (req.method === "HEAD") {
  console.log("[HEAD HANDLER] Responding to HEAD request.");
  return new Response(null, {
    status: 200,
    headers: corsHeaders
  });
}
  let supabaseClient; // Объявляем здесь, чтобы была доступна в catch блоке для GET
  const requestBody = req.method === "POST" ? await req.json().catch((e)=>{
    console.error(`[REQUEST PARSE ERROR] Failed to parse JSON body for ${req.method} request:`, e.message);
    return null; // Возвращаем null, чтобы обработать ошибку позже
  }) : null;
  try {
    if (req.method === "POST") {
      console.log("[POST HANDLER] Received POST request. Authenticating user via JWT.");
      const authHeader = req.headers.get("Authorization");
      if (!authHeader) {
        console.error("[POST HANDLER] Missing Authorization header.");
        throw new Error("Missing Authorization header");
      }
      supabaseClient = createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_ANON_KEY") ?? "", {
        global: {
          headers: {
            Authorization: authHeader
          }
        }
      });
      console.log("[POST HANDLER] Supabase client initialized with user's JWT.");
      const { data: { user }, error: userError } = await supabaseClient.auth.getUser();
      if (userError) {
        console.error("[POST HANDLER] Error getting user:", userError.message);
        return new Response(JSON.stringify({
          error: "Authentication failed: " + userError.message
        }), {
          status: 401,
          headers: {
            ...corsHeaders,
            "Content-Type": "application/json"
          }
        });
      }
      if (!user) {
        console.error("[POST HANDLER] User not authenticated.");
        return new Response(JSON.stringify({
          error: "User not authenticated"
        }), {
          status: 401,
          headers: {
            ...corsHeaders,
            "Content-Type": "application/json"
          }
        });
      }
      console.log("[POST HANDLER] User authenticated:", user.id);
      if (!requestBody) {
        console.error("[POST HANDLER] Invalid or missing JSON body.");
        return new Response(JSON.stringify({
          error: "Invalid or missing JSON body"
        }), {
          status: 400,
          headers: {
            ...corsHeaders,
            "Content-Type": "application/json"
          }
        });
      }
      const action = requestBody.action; // Ожидаем 'action' в теле
      const portalUrlFromBody = requestBody.portal_url;
      console.log("[POST HANDLER] Request body processed. Action:", action, "Portal URL:", portalUrlFromBody);
      if (action === 'refresh_token') {
        console.log("[POST HANDLER - REFRESH TOKEN] Processing refresh token request.");
        const refreshToken = requestBody.refresh_token;
        if (!portalUrlFromBody || !refreshToken) {
          console.error("[POST HANDLER - REFRESH TOKEN] Missing portal_url or refresh_token for refresh action.");
          return new Response(JSON.stringify({
            error: "Missing portal_url or refresh_token for refresh action"
          }), {
            status: 400,
            headers: {
              ...corsHeaders,
              "Content-Type": "application/json"
            }
          });
        }
        const refreshTokenUrl = `https://${portalUrlFromBody}/oauth/token/?grant_type=refresh_token&client_id=${BITRIX_CLIENT_ID}&client_secret=${BITRIX_CLIENT_SECRET}&refresh_token=${refreshToken}`;
        console.log("[POST HANDLER - REFRESH TOKEN] Refresh token URL to Bitrix:", refreshTokenUrl);
        const refreshResponse = await fetch(refreshTokenUrl);
        console.log("[POST HANDLER - REFRESH TOKEN] Bitrix refresh token response status:", refreshResponse.status);
        if (!refreshResponse.ok) {
          const errorText = await refreshResponse.text();
          console.error("[POST HANDLER - REFRESH TOKEN] Error refreshing Bitrix24 token. Status:", refreshResponse.status, "Response:", errorText);
          return new Response(JSON.stringify({
            error: "Failed to refresh Bitrix24 token",
            details: errorText
          }), {
            status: refreshResponse.status,
            headers: {
              ...corsHeaders,
              "Content-Type": "application/json"
            }
          });
        }
        const newTokens = await refreshResponse.json();
        console.log("[POST HANDLER - REFRESH TOKEN] New tokens received from Bitrix:", JSON.stringify(newTokens).substring(0, 200) + "...");
        if (newTokens.error) {
          console.error("[POST HANDLER - REFRESH TOKEN] Error in Bitrix24 refresh token response object:", newTokens.error, newTokens.error_description);
          return new Response(JSON.stringify({
            error: newTokens.error_description || "Bitrix24 refresh token error",
            b24_error: newTokens.error
          }), {
            status: 400,
            headers: {
              ...corsHeaders,
              "Content-Type": "application/json"
            }
          });
        }
        // Используем supabaseClient, инициализированный с SERVICE_ROLE_KEY для сохранения токенов,
        // так как пользовательский JWT может не иметь прав на запись в bitrix_integrations
        // или это более безопасно делать с service role.
        // Если вы уверены, что JWT пользователя имеет нужные права, можно использовать supabaseClient,
        // который уже был создан с authHeader. Для большей безопасности обычно используют service_role.
        const serviceRoleSupabaseClient = createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "");
        await saveTokens(serviceRoleSupabaseClient, user.id, portalUrlFromBody, newTokens);
        console.log("[POST HANDLER - REFRESH TOKEN] New tokens saved. Responding to client.");
        return new Response(JSON.stringify({
          access_token: newTokens.access_token,
          refresh_token: newTokens.refresh_token,
          expires_in: newTokens.expires_in
        }), {
          headers: {
            ...corsHeaders,
            "Content-Type": "application/json"
          }
        });
      } else {
        console.log("[POST HANDLER - GET AUTH URL] Processing get authorization URL request.");
        const stateString = requestBody.state; // state для URL авторизации
        if (!portalUrlFromBody || !stateString) {
          console.error("[POST HANDLER - GET AUTH URL] Missing portal_url or state in request body for get_auth_url action.");
          return new Response(JSON.stringify({
            error: "Missing portal_url or state in request body for get_auth_url action"
          }), {
            status: 400,
            headers: {
              ...corsHeaders,
              "Content-Type": "application/json"
            }
          });
        }
        const authRedirectUrl = `https://${portalUrlFromBody}/oauth/authorize/?client_id=${BITRIX_CLIENT_ID}&response_type=code&redirect_uri=${encodeURIComponent(FUNCTION_REDIRECT_URI)}&state=${encodeURIComponent(stateString)}`;
        console.log("[POST HANDLER - GET AUTH URL] Generated Bitrix Auth URL:", authRedirectUrl);
        return new Response(JSON.stringify({
          authorization_url: authRedirectUrl
        }), {
          headers: {
            ...corsHeaders,
            "Content-Type": "application/json"
          }
        });
      }
    } else if (req.method === "GET") {
      console.log("[GET HANDLER] Received GET request. Attempting to use SERVICE_ROLE_KEY.");
      console.log("[GET HANDLER] SUPABASE_URL:", Deno.env.get("SUPABASE_URL"));
      console.log("[GET HANDLER] SUPABASE_SERVICE_ROLE_KEY is set:", !!Deno.env.get("SUPABASE_SERVICE_ROLE_KEY"));
      supabaseClient = createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "");
      console.log("[GET HANDLER] Supabase client initialized for service role.");
      const url = new URL(req.url);
      const errorFromBitrixAuth = url.searchParams.get("error");
      const errorDescriptionFromBitrixAuth = url.searchParams.get("error_description");
      const errorCodeFromBitrixAuth = url.searchParams.get("error_code");
      if (errorFromBitrixAuth) {
        let details = errorDescriptionFromBitrixAuth || "Bitrix OAuth authorization error before code exchange.";
        if (errorCodeFromBitrixAuth) {
          details += ` (Code: ${errorCodeFromBitrixAuth})`;
        }
        if (errorFromBitrixAuth === 'access_denied' || errorDescriptionFromBitrixAuth && (errorDescriptionFromBitrixAuth.toLowerCase().includes('application not found') || errorDescriptionFromBitrixAuth.toLowerCase().includes('application not installed') || errorDescriptionFromBitrixAuth.toLowerCase().includes('приложение не найдено') || errorDescriptionFromBitrixAuth.toLowerCase().includes('приложение не установлено'))) {
          const specificErrorDetail = `Приложение Brifia не установлено или неактивно на вашем портале Bitrix24. Детали: ${details}`;
          console.warn(`[GET HANDLER] Application not installed error (direct from Bitrix): ${specificErrorDetail}`);
          return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=APPLICATION_NOT_INSTALLED&details=${encodeURIComponent(specificErrorDetail)}`, 302);
        }
        console.warn(`[GET HANDLER] Bitrix returned an error directly in authorization redirect: ${errorFromBitrixAuth}, Description: ${details}`);
        return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=b24_auth_redirect_error&b24_error=${encodeURIComponent(errorFromBitrixAuth)}&details=${encodeURIComponent(details)}`, 302);
      }
      const code = url.searchParams.get("code");
      const stateString = url.searchParams.get("state");
      console.log("[GET HANDLER] Code from Bitrix:", code);
      console.log("[GET HANDLER] State string from Bitrix:", stateString);
      if (!code || !stateString) {
        console.error("[GET HANDLER] Missing code or state in query parameters from Bitrix (and no direct error parameter was found).");
        return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=missing_code_or_state&details=Code_or_state_is_missing_from_Bitrix_redirect_and_no_direct_error_was_found.`, 302);
      }
      let statePayload;
      try {
        statePayload = JSON.parse(decodeURIComponent(stateString));
        console.log("[GET HANDLER] Parsed state payload:", statePayload);
      } catch (e) {
        console.error("[GET HANDLER] Error parsing state:", e.message, "Original state string:", stateString);
        return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=invalid_state_format&details=${encodeURIComponent(e.message)}`, 302);
      }
      const { userId, portalUrl: portalUrlFromState } = statePayload;
      if (!userId || !portalUrlFromState) {
        console.error("[GET HANDLER] State is missing userId or portalUrl. Parsed state:", statePayload);
        return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=invalid_state_payload_content&details=State_is_missing_userId_or_portalUrl.`, 302);
      }
      console.log(`[GET HANDLER] Processing for Supabase user ID: ${userId}, Portal from state: ${portalUrlFromState}`);
      const tokenUrl = `https://${portalUrlFromState}/oauth/token/?grant_type=authorization_code&client_id=${BITRIX_CLIENT_ID}&client_secret=${BITRIX_CLIENT_SECRET}&code=${code}&redirect_uri=${encodeURIComponent(FUNCTION_REDIRECT_URI)}`;
      console.log("[GET HANDLER] Token exchange URL to Bitrix:", tokenUrl);
      const tokenResponse = await fetch(tokenUrl);
      console.log("[GET HANDLER] Bitrix token exchange response status:", tokenResponse.status);
      if (!tokenResponse.ok) {
        const errorText = await tokenResponse.text();
        console.error("[GET HANDLER] Error fetching Bitrix24 token. Status:", tokenResponse.status, "Response:", errorText);
        let b24ErrorDetails = errorText;
        try {
          const parsedError = JSON.parse(errorText);
          if (parsedError.error_description) {
            b24ErrorDetails = parsedError.error_description;
          } else if (parsedError.error) {
            b24ErrorDetails = parsedError.error;
          }
        } catch (e) {}
        if (b24ErrorDetails.toLowerCase().includes('application not found') || b24ErrorDetails.toLowerCase().includes('application not installed') || b24ErrorDetails.toLowerCase().includes('приложение не найдено') || b24ErrorDetails.toLowerCase().includes('приложение не установлено') || tokenResponse.status === 400 && (b24ErrorDetails.toLowerCase().includes('invalid_client') || b24ErrorDetails.toLowerCase().includes('client is invalid'))) {
          const specificErrorDetail = `Приложение Brifia не установлено, неактивно или неверно сконфигурировано на портале ${portalUrlFromState}. Детали: ${b24ErrorDetails}`;
          console.warn(`[GET HANDLER] Application not installed error (during token exchange): ${specificErrorDetail}`);
          return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=APPLICATION_NOT_INSTALLED&details=${encodeURIComponent(specificErrorDetail)}`, 302);
        }
        return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=b24_token_exchange_failed&status=${tokenResponse.status}&details=${encodeURIComponent(b24ErrorDetails)}`, 302);
      }
      const tokens = await tokenResponse.json();
      console.log("[GET HANDLER] Tokens received from Bitrix:", JSON.stringify(tokens).substring(0, 200) + "...");
      if (tokens.error) {
        console.error("[GET HANDLER] Error in Bitrix24 token response object:", tokens.error, tokens.error_description);
        let errorDescription = tokens.error_description || 'Unknown Bitrix token error';
        if (tokens.error.toLowerCase().includes('invalid_grant') || errorDescription.toLowerCase().includes('application not found') || errorDescription.toLowerCase().includes('application not installed') || errorDescription.toLowerCase().includes('приложение не найдено') || errorDescription.toLowerCase().includes('приложение не установлено')) {
          const specificErrorDetail = `Приложение Brifia не установлено, неактивно или неверно сконфигурировано на портале. Детали: ${errorDescription}`;
          console.warn(`[GET HANDLER] Application not installed error (in token response object): ${specificErrorDetail}`);
          return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=APPLICATION_NOT_INSTALLED&details=${encodeURIComponent(specificErrorDetail)}`, 302);
        }
        return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=b24_token_error&b24_error=${encodeURIComponent(tokens.error)}&details=${encodeURIComponent(errorDescription)}`, 302);
      }
      try {
        console.log("[GET HANDLER] Attempting to save tokens to Supabase...");
        await saveTokens(supabaseClient, userId, portalUrlFromState, tokens);
        console.log("[GET HANDLER] Tokens saved. Redirecting to success page:", FINAL_SUCCESS_REDIRECT_URI);
        return Response.redirect(FINAL_SUCCESS_REDIRECT_URI, 302);
      } catch (saveError) {
        console.error("[GET HANDLER] Error during saveTokens or success redirect:", saveError.message, saveError.stack);
        return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=save_tokens_failed&details=${encodeURIComponent(saveError.message)}`, 302);
      }
    } else {
      console.warn(`[REQUEST HANDLER] Method Not Allowed: ${req.method}`);
      return new Response("Method Not Allowed", {
        status: 405,
        headers: {
          ...corsHeaders,
          "Content-Type": "text/plain"
        }
      });
    }
  } catch (error) {
    console.error("[GLOBAL ERROR HANDLER] Error processing request. Message:", error.message, "Stack:", error.stack, "Full error object:", JSON.stringify(error, Object.getOwnPropertyNames(error)));
    const errorMessage = error.message || "Unknown server error";
    if (req.method === "GET") {
      console.log("[GLOBAL ERROR HANDLER] Redirecting GET request to error page due to unhandled error:", errorMessage);
      return Response.redirect(`${FINAL_ERROR_REDIRECT_URI}?error=internal_server_error&details=${encodeURIComponent(errorMessage)}`, 302);
    }
    // Для POST и других методов возвращаем JSON ошибку
    console.log("[GLOBAL ERROR HANDLER] Returning JSON error for non-GET request:", errorMessage);
    return new Response(JSON.stringify({
      error: "Internal Server Error: " + errorMessage
    }), {
      status: 500,
      headers: {
        ...corsHeaders,
        "Content-Type": "application/json"
      }
    });
  }
});
