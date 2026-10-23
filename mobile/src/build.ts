import Constants from "expo-constants";

// The build a Saved Build's caption names (ADR 43 Q5), as the foot of Settings
// shows it (ADR 45 Q4): `Enloq <version> · <commit> · <build date>`. A development build
// carries no commit; Metro serves its code.
export type BuildDetails = { commit?: string; time?: string };

export function buildLine(version: string, build?: BuildDetails | null) {
  if (!build?.commit) return `Enloq ${version} · development build`;
  const date = /^\d{4}-\d{2}-\d{2}/.exec(build.time ?? "")?.[0];
  return [`Enloq ${version}`, build.commit, date].filter(Boolean).join(" · ");
}

export function thisBuild() {
  const config = Constants.expoConfig;
  return buildLine(
    config?.version ?? "",
    (config?.extra as { build?: BuildDetails } | undefined)?.build,
  );
}
