import type { ConfigContext, ExpoConfig } from "expo/config";
import {
  AndroidConfig,
  type ConfigPlugin,
  withAndroidManifest,
  withAppBuildGradle,
  withDangerousMod,
  withEntitlementsPlist,
} from "expo/config-plugins";
import fs from "node:fs";
import path from "node:path";

// Debug builds install beside the owner's release build as Enloq Dev
// (com.globesoul.enloq.dev), so fixture journeys never touch the pairing the
// owner made with the VPS. Release builds keep Enloq's own ID (Q28).
const withDevelopmentVariant: ConfigPlugin = (config) => {
  config = withAppBuildGradle(config, (gradle) => {
    if (!gradle.modResults.contents.includes('applicationIdSuffix ".dev"'))
      gradle.modResults.contents = gradle.modResults.contents.replace(
        /buildTypes\s*\{\s*debug\s*\{/,
        (opening) => `${opening}\n            applicationIdSuffix ".dev"`,
      );
    return gradle;
  });
  return withDangerousMod(config, [
    "android",
    async (mod) => {
      const values = path.join(
        mod.modRequest.platformProjectRoot,
        "app/src/debug/res/values",
      );
      fs.mkdirSync(values, { recursive: true });
      fs.writeFileSync(
        path.join(values, "strings.xml"),
        '<resources>\n  <string name="app_name">Enloq Dev</string>\n</resources>\n',
      );
      return mod;
    },
  ]);
};

// Development builds open straight into the app: no developer-menu
// introduction or menu at launch and no floating button over the header, so
// journeys (MC5) start from the app's own screen. The menu still opens with
// a shake, Ctrl-D on the simulator or Android's menu key; release builds have
// no developer menu and ignore these keys.
const DEV_MENU = {
  EXDevMenuIsOnboardingFinished: true,
  EXDevMenuShowsAtLaunch: false,
  EXDevMenuShowFloatingActionButton: false,
};

const withQuietDevMenu: ConfigPlugin = (config) =>
  withAndroidManifest(config, (mod) => {
    const app = AndroidConfig.Manifest.getMainApplicationOrThrow(
      mod.modResults,
    );
    for (const [name, value] of Object.entries(DEV_MENU))
      AndroidConfig.Manifest.addMetaDataItemToMainApplication(
        app,
        name,
        String(value),
      );
    return mod;
  });

// ADR 38: Firebase starts nothing on its own, so
// Enloq first contacts Google when notifications are turned on (Q42).
const withQuietFirebase: ConfigPlugin = (config) =>
  withAndroidManifest(config, (mod) => {
    const app = AndroidConfig.Manifest.getMainApplicationOrThrow(
      mod.modResults,
    );
    for (const name of [
      "firebase_messaging_auto_init_enabled",
      "firebase_analytics_collection_enabled",
    ])
      AndroidConfig.Manifest.addMetaDataItemToMainApplication(
        app,
        name,
        "false",
      );
    return mod;
  });

// expo-notifications adds the iOS push entitlement, which free Personal Team
// signing rejects; iOS push waits for enrollment (Q26-Q27).
const withoutPushEntitlement: ConfigPlugin = (config) =>
  withEntitlementsPlist(config, (mod) => {
    delete mod.modResults["aps-environment"];
    return mod;
  });

// Firebase's Android config carries the app's API key, so it stays out of
// Git beside the app; a build without it has no push.
const GOOGLE_SERVICES = "./google-services.json";

// ADR 43 Q5: a Saved Build carries its commit and build time, which
// mobile/scripts/publish-android sets; other builds carry neither.
const BUILD = {
  commit: process.env.ENLOQ_BUILD_COMMIT ?? "",
  time: process.env.ENLOQ_BUILD_TIME ?? "",
};

// ADR 43 Q8: every build offers Connect to its release server, set at build
// time; the journeys point Enloq Dev's at the local fixture through Metro.
const SERVER =
  process.env.ENLOQ_RELEASE_SERVER || "https://enloq.globesoul.com";

// ADR 38 Q26: iPhone builds are signed by the owner's free Personal Team.
// Its team ID comes from the environment, never from the repository.
export default ({ config, projectRoot }: ConfigContext): ExpoConfig => {
  const team = process.env.ENLOQ_APPLE_TEAM_ID;
  const firebase = fs.existsSync(path.join(projectRoot, GOOGLE_SERVICES));
  return withoutPushEntitlement(
    withQuietFirebase(
      withQuietDevMenu(
        withDevelopmentVariant({
          ...(config as ExpoConfig),
          extra: {
            ...config.extra,
            server: SERVER,
            ...(BUILD.commit ? { build: BUILD } : {}),
          },
          android: {
            ...config.android,
            ...(firebase ? { googleServicesFile: GOOGLE_SERVICES } : {}),
          },
          ios: {
            ...config.ios,
            infoPlist: { ...config.ios?.infoPlist, ...DEV_MENU },
            ...(team ? { appleTeamId: team } : {}),
          },
        }),
      ),
    ),
  );
};
