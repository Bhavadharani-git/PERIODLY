# PERIODLY Android app

This folder packages the existing PERIODLY web frontend as an Android application with Capacitor. It does not create a second frontend or a second database. Timetable generation continues to run on the PERIODLY FastAPI server using the existing OR-Tools CP-SAT optimizer; web and Android use the same API and PostgreSQL database.

## Requirements

- Node.js 22 or later (Node.js 26 LTS is suitable).
- Android Studio 2025.2.1 or later for Capacitor 8.
- Android SDK Platform 24 or later; use a currently supported SDK installed through Android Studio's SDK Manager.
- A deployed PERIODLY API reachable through HTTPS before preparing the release APK.

Official references:
- https://capacitorjs.com/docs/getting-started/environment-setup
- https://capacitorjs.com/docs/android
- https://capacitorjs.com/docs/getting-started

## Important security notes

- Never put `PERIODLY_API_KEY`, a database password, or a PostgreSQL connection URL in `frontend/config.js`, `mobile/www/config.js`, `capacitor.config.json`, the Android project, or the APK.
- The existing app asks the administrator to enter the API key at runtime after the API returns HTTP 401. The frontend keeps it in tab/session storage, not in the source code. Capacitor's Android WebView supports JavaScript prompt dialogs.
- The current API key is a shared administrator gate, not individual user authentication. Keep the app restricted to the authorized school administrator until full user accounts and roles are implemented.
- The mobile app needs internet access. It does not generate timetables offline; the cloud backend runs the CP-SAT solver.
- Since the Android WebView serves its local app from `https://localhost`, set the production backend's `CORS_ORIGINS` environment variable to exactly `https://localhost`. For the same-origin web app, no additional CORS origin is required. Do not use `*`.
- The API must be served over HTTPS. Do not enable mixed content or use an HTTP endpoint in a release APK.

## Prepare the mobile web bundle (after the API has been deployed)

Run this from PowerShell in the `mobile` directory. Replace the example host with the actual HTTPS Render service URL:

```powershell
.\scripts\Prepare-Mobile.ps1 -ApiBaseUrl "https://YOUR-RENDER-SERVICE.onrender.com/api"
```

The script copies the canonical `../frontend` files into the generated `www` folder and writes a `config.js` containing only the public API base URL. It validates that the URL is HTTPS and ends in `/api`. The generated `www` folder is ignored by Git because it is reproducibly generated from the tracked frontend source.

## Install Capacitor and generate Android project

From the `mobile` directory:

```powershell
npm install
npx cap add android
npx cap sync android
npx cap open android
```

`npx cap add android` is a one-time command. For later web frontend updates, rerun the prepare script and then `npx cap sync android`.

## Build an APK

In Android Studio:

1. Wait for Gradle sync to finish.
2. Select **Build > Build Bundle(s) / APK(s) > Build APK(s)**.
3. When the build completes, select **Locate**.
4. The debug APK is normally at `android/app/build/outputs/apk/debug/app-debug.apk`.

For a command-line debug build, from the `mobile` directory:

```powershell
.\android\gradlew.bat assembleDebug
```

The APK is a test build. Before distributing outside the administrator's test device, create a signed release APK/AAB in Android Studio and complete the required security and school-data tests.

## Test checklist

- Install the APK on a real Android phone.
- Confirm the PERIODLY sign-in/admin-key prompt appears after first API access.
- Enter the administrator key only in the app prompt; never paste it into a project file.
- Verify school, class, teacher and subject lists load from the cloud API.
- Verify timetable view and reports load.
- Run one test generation only after confirming the cloud database contains a verified backup/import of the school data.
- Confirm a timetable change made on web appears in Android after refresh, and vice versa.
- Test on mobile data and Wi-Fi; verify all requests use HTTPS.

## Git notes

Commit `mobile/package.json`, `mobile/capacitor.config.json`, `mobile/.gitignore`, `mobile/scripts/`, and this README. Do not commit `mobile/node_modules/`, `mobile/www/`, Android build outputs, signing keys, or local signing properties. After `npx cap add android`, commit the generated `mobile/android/` project source, but keep its Gradle build outputs and local machine settings ignored.
