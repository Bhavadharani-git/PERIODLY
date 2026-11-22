# PERIODLY Android mobile rollout

This is an add-on to the existing PERIODLY GitHub repository. It reuses `frontend/` and the existing FastAPI service. It does not duplicate the scheduler or database.

## What to copy into the existing repository

Copy the `mobile/` directory and `.github/workflows/ci-mobile.yml` from this add-on into the repository root. Do not replace the existing backend, frontend, Docker or deployment files.

## Platform versions

This addon pins the official Capacitor packages to 8.6.2. Capacitor 8 requires Node.js 22 or newer, Android Studio 2025.2.1 or newer, and Android SDK Platform 24 or newer. The Node.js 26 LTS line is recommended for a new installation. See https://capacitorjs.com/docs/getting-started/environment-setup and https://capacitorjs.com/docs/android.

## Sequence

1. Merge this add-on into the existing GitHub-backed local repository.
2. Commit and push `mobile/` plus `.github/workflows/ci-mobile.yml`.
3. Deploy the API over HTTPS and configure a persistent PostgreSQL database.
4. On the production backend, set `CORS_ORIGINS=https://localhost` for the native Android WebView origin. Keep the API key secret; do not add it to the app bundle.
5. From `mobile/`, prepare a bundle against the actual deployment URL:

   ```powershell
   .\scripts\Prepare-Mobile.ps1 -ApiBaseUrl "https://YOUR-RENDER-SERVICE.onrender.com/api"
   ```

6. Install and create the native project (only run `cap add android` once):

   ```powershell
   npm install
   npx cap add android
   npx cap sync android
   npx cap open android
   ```

7. In Android Studio, wait for Gradle sync and choose **Build > Build Bundle(s) / APK(s) > Build APK(s)**. The debug APK is normally `mobile/android/app/build/outputs/apk/debug/app-debug.apk`.
8. Test on a real Android device. Enter the administrator key only when the app prompts; do not hard-code it.

## Important constraints

- The app is online-first: generation and database access are performed by the hosted API, not on the phone.
- The current shared administrator API key is a restricted pilot control, not individual accounts or role-based access. Do not distribute this APK broadly until proper accounts, roles, and school-level authorization are implemented.
- Render free services may sleep; first request can be slow. Use a free database only for a test/pilot and maintain an independent backup. Do not assume free hosting provides production-grade durability.
- A local APK cannot be built until a real HTTPS API URL exists. The prepare script intentionally rejects HTTP and placeholder/invalid endpoints.
- The generated `mobile/www/` bundle is ignored by Git and must be regenerated from the tracked root `frontend/` before each Capacitor sync.
- After `npx cap add android`, review and commit the generated `mobile/android/` source project, but do not commit build output, local SDK settings, keystores, or signing passwords.
