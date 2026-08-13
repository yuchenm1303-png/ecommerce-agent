# Listing Studio Download Portal

Independent static download portal for the Windows Listing Studio application.

## Preview

Serve this directory with any static server:

```bash
python -m http.server 8080 -d download-site
```

Then open `http://127.0.0.1:8080`.

## Visual system

- Full-screen wallpaper layer with a dark readability veil.
- Frosted cards (`backdrop-filter`) and subtle scale hover/press feedback.
- Lightweight CSS sakura petals and a requestAnimationFrame cursor follower.
- Desktop-first two-column composition that collapses to one column below 1120px.
- No framework/build step is required for the portal shell.

## Real login

The UI is production-shaped but authentication remains disabled until Supabase is configured in `config.js`:

- `auth.supabaseUrl`
- `auth.supabaseAnonKey`
- `auth.downloadFunctionUrl`

The frontend uses Supabase email/password login. For production downloads, `downloadFunctionUrl` should validate the user's access token and return a short-lived signed URL to the current installer. Do not expose a permanent private installer URL in client source.

## Release data

`config.js` currently owns the preview version, release notes and platform data. The next integration step is to have the Windows packaging workflow publish release metadata automatically instead of editing this file by hand.
