# Setup guide: Meta (Instagram + Facebook) and YouTube

> Meta and Google rename menus often. If a button has moved, look for the same words nearby.
> **Never paste tokens, secrets or passwords into chat or git.** They go only into `.env` on
> the VPS (`chmod 600 .env`) or into the `secrets/` folder there.

---

## Part A: Meta (Instagram + Facebook Page)

### A1. Prepare the accounts (about 10 min)
1. **Facebook account**: log in to your personal Facebook account. Turn on **two-factor
   authentication** (Settings → Accounts Center → Password and security). Meta often blocks
   developer sign-up for brand-new accounts that have no 2FA or phone number.
2. **Facebook Page**: facebook.com → Pages → **Create new Page**. Use the same name as the
   Instagram page, category "Spirituality" or "Community". Add the profile picture.
3. **Instagram Professional account**: Instagram app → Profile → ☰ → **Account type and tools**
   → **Switch to professional account** → choose **Creator** → category "Digital creator" or
   "Spirituality". (A Business account also works. Creator gives better monetization tools.)
4. **Link Instagram to the Page**: Facebook Page → Settings → **Linked accounts** → Instagram →
   Connect. (This is only for cross-posting and Page insights. Instagram publishing does not
   need it.)

### A2. Create the developer account and app (about 15 min)
1. Go to **developers.facebook.com** → **Get Started**. Verify your phone/email and accept the
   terms. Choose role "Developer".
2. **My Apps** → **Create App**.
   - App name: e.g. `InstaPost Agent` (don't use "Instagram" or "Facebook" in the name, Meta rejects it).
   - Contact email: your email.
   - Use case: choose **"Manage messaging & content on Instagram"**. Then on the next screen
     also add **"Manage everything on your Page"** (for Facebook Reels). If the wizard only
     allows one, add the second later from **Use cases → Add use case**.
   - Business portfolio: "I don't want to connect a business portfolio yet" is fine.
3. Open the app → **App settings → Basic**. Note the **App ID** and **App Secret** (click Show).
   They go into `.env` as `META_APP_ID` and `META_APP_SECRET`.
4. Add a **Privacy Policy URL** (required even in development mode). A simple page is enough,
   for example a public Google Doc or a GitHub Pages page. Phase 2 can host one on the dashboard
   domain.

> **Development mode is enough for us.** An app in development mode can use the API on
> accounts of people who have a role on the app (you). So we do **not** need App Review or
> business verification to post to our own accounts. Leave the app in development mode.

### A3. Instagram API with Instagram Login (about 10 min)
1. App dashboard → **Use cases** → *Manage messaging & content on Instagram* → **Customize** →
   **API setup with Instagram login**.
2. **Step 1 – Permissions:** make sure these are added:
   `instagram_business_basic`, `instagram_business_content_publish`,
   `instagram_business_manage_insights`, `instagram_business_manage_comments`.
3. **Step 2 – Generate access tokens:** click **Add account** and log in with your Instagram
   professional account. Approve all permissions.
   - If it says the account isn't a tester: go to **App roles → Roles → Add People →
     Instagram Tester**, enter your Instagram username, then in the Instagram app go to
     Settings → **Website permissions → Apps and websites → Tester invites** → Accept.
4. Click **Generate token** next to the account. Copy it right away; it's shown once. This is a
   **long-lived token (~60 days)**. The agent refreshes it automatically before it expires.
   → `.env`: `IG_ACCESS_TOKEN`
5. The same screen shows the **Instagram user ID** (a long number).
   → `.env`: `IG_USER_ID`
6. You can skip "Configure webhooks" and "Set up Instagram business login" for now.

### A4. Facebook Page token for Facebook Reels (about 10 min)
1. Open **developers.facebook.com/tools/explorer** (Graph API Explorer).
2. Top right: select your app. **User or Page** → "Get User Access Token".
3. Add permissions: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `read_insights`, `business_management`. Click **Generate Access Token** and approve.
   Choose your Page when asked.
4. Turn it into a long-lived token: open **developers.facebook.com/tools/debug/accesstoken**,
   paste the token → **Debug** → **Extend Access Token** at the bottom. Copy the new token.
5. Back in Graph API Explorer: paste the long-lived token in the token box, run
   `GET me/accounts`. In the result, find your Page: copy its **id** and **access_token**.
   A Page token taken from a long-lived user token **does not expire** (it can still be revoked
   if you change your password).
   → `.env`: `FB_PAGE_ID`, `FB_PAGE_TOKEN`
6. Check it: paste the Page token in the Access Token Debugger. It should say
   **Type: Page** and **Expires: Never**.

### A5. What goes in `.env` from Meta
```
META_APP_ID=...
META_APP_SECRET=...
IG_USER_ID=...
IG_ACCESS_TOKEN=...
FB_PAGE_ID=...
FB_PAGE_TOKEN=...
```

---

## Part B: YouTube (Shorts upload + analytics)

### B1. Channel (about 10 min)
1. Log in to youtube.com with the Google account you want to use → profile picture →
   **Create a channel**. Use the page name and handle.
2. Go to **youtube.com/verify** and verify by phone. This unlocks custom thumbnails and
   removes some upload limits.
3. Optional: upload 1–2 Shorts by hand from the phone this week, so the channel isn't empty.

### B2. Google Cloud project (about 15 min)
1. Go to **console.cloud.google.com** with the **same Google account** that owns the channel.
   Accept the terms. **No billing account is needed** for the YouTube Data API.
2. Top bar → project picker → **New Project** → name `instapost` → Create. Select it.
3. **APIs & Services → Library** → enable:
   - **YouTube Data API v3**
   - **YouTube Analytics API**
4. **Google Auth Platform** (older name: "OAuth consent screen") → **Get started**:
   - App name `InstaPost Agent`, support email = your email.
   - Audience: **External**.
   - Contact email → Finish.
5. **Audience** → **Test users** → **Add users** → your channel's Gmail address.
6. **Data access** → **Add or remove scopes** → add:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube.readonly`
   - `https://www.googleapis.com/auth/yt-analytics.readonly`
   → Save.
7. **Clients** → **Create client** → Application type **Desktop app** → name `instapost-vps`
   → Create → **Download JSON**. Keep this file safe; it's a secret.
   On the VPS, save it as `secrets/youtube_client_secret.json` (`chmod 600`).

### B3. Avoid the two YouTube traps
1. **7-day token expiry.** While the app's publishing status is **Testing**, Google expires
   the login every 7 days, and the agent would stop uploading weekly. Fix: **Audience →
   Publish app → In production**. Google will show an "unverified app" warning when *you*
   log in. That's expected for a personal tool; click **Advanced → Go to InstaPost Agent
   (unsafe)**. Nobody else uses this app, so no Google verification is needed.
2. **Uploads forced to private.** YouTube locks videos uploaded through the API by new,
   unaudited projects to **private**. Two options:
   - Short term: the agent uploads as private, and you make it public in the YouTube Studio
     app (2 taps). The agent will remind you in the Telegram approval message.
   - Long term: submit the **YouTube API Services – Audit and Quota Extension Form**
     (search that name; it's on support.google.com/youtube). Describe it as "a personal tool
     that uploads my own channel's Shorts, created by me". Approval usually takes a few weeks.

### B4. Connect the agent (in Phase 2)
I'll add a command `instapost youtube-login`. You run it once; it prints a Google link, you
log in with the channel account and approve, and the agent stores the refresh token in
`secrets/` on the VPS. Nothing to do until then.

---

## Checklist
- [ ] Facebook 2FA on, Facebook Page created
- [ ] Instagram switched to Creator
- [ ] Meta app created (development mode), privacy policy URL set
- [ ] Instagram token + user ID generated
- [ ] Facebook Page ID + never-expiring Page token
- [ ] YouTube channel created and phone-verified
- [ ] Google Cloud project, both APIs enabled
- [ ] OAuth consent: External, test user added, scopes added, **In production**
- [ ] Desktop OAuth client JSON downloaded to the VPS
- [ ] (Optional) YouTube API audit form submitted
