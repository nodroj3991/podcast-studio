# One-time keys and logins (all free, all optional)

The same instructions are shown inside the app under **Settings → Keys & logins**.

## Speaker detection — Hugging Face token
Without it the app uses a built-in "basic" voice separator (about 85–90 % right on two distinct voices). With it, pyannote is used (near-perfect on clean two-person audio).
1. Create a free account at https://huggingface.co/join
2. Open https://huggingface.co/pyannote/speaker-diarization-3.1 and https://huggingface.co/pyannote/segmentation-3.0 → click **Agree and access repository** on each.
3. https://huggingface.co/settings/tokens → **Create new token** (type *Read*) → copy → paste into Settings.
Note: if your recording has **one file per person**, speakers are taken from the files and no model is needed at all.

## Music — Jamendo client ID
Openverse works with no key. Adding Jamendo gives a much larger catalogue with mood tags.
1. https://devportal.jamendo.com/ → sign up → **Create an app** (any name, any URL).
2. Copy the **Client ID** into Settings.

## Research charts — contact email
OpenAlex and Crossref are free with no key; entering your email in Settings puts requests in their faster "polite pool". Semantic Scholar is used without a key (optional key raises the rate limit).

## Canva — Connect API (to send charts, title cards and thumbnails to your Canva account)
1. https://www.canva.com/developers/integrations/connect-api → **Create an integration** (type: *Private* is fine for your own team).
2. **Scopes**: `asset:read`, `asset:write`, `design:content:write`, `design:meta:read`.
3. **Authentication → Add URL**: `http://127.0.0.1:5890/canva/callback`
4. Copy **Client ID**; click **Generate secret** and copy it. Paste both in Settings → **Save settings** → **Connect Canva** → approve in the browser.
After that, every episode page has **Send to Canva**: it uploads the thumbnail, title card, lower-third and chart PNGs to *Uploads* and opens a new 1280×720 thumbnail design.

## GitHub (only if you want cloud builds and in-app updates)
A free account; see `BUILD.md`.
