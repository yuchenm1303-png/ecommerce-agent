# Listing Studio desktop social login

The packaged Listing Studio login dialog supports the same Supabase Google and
GitHub identities as the download portal.

Flow:

1. Listing Studio binds 127.0.0.1:47843 for a short-lived callback.
2. The app creates an RFC 7636 PKCE verifier/challenge and opens the system
   browser at the Supabase social authorization endpoint.
3. Google/GitHub authenticates through the existing Supabase provider.
4. Supabase redirects the browser to
   http://127.0.0.1:47843/oauth/callback?code=...
5. The app exchanges the one-time Auth Code with the PKCE verifier.
6. The resulting Supabase access token still goes through portal-license
   with action=activate. Social sign-in never bypasses Listing Studio account
   or device authorization.
7. Only the Supabase refresh token and existing Listing Studio device state are
   stored through the current DPAPI-protected local state.

## Required Supabase URL configuration

Add this exact URL under Authentication → URL Configuration → Redirect URLs:

    http://127.0.0.1:47843/oauth/callback

The Google/GitHub provider callback registered with the providers remains the
normal Supabase callback:

    https://nfzkphjbelyltrzgkdwt.supabase.co/auth/v1/callback

No provider client secret is embedded in Listing Studio.
