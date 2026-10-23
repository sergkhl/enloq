import { Platform } from "react-native";

// Android push through FCM (ADR 38). iOS push waits
// for Apple Developer enrollment (Q27), so the phone's settings stay hidden
// there.
export const phonePush = Platform.OS === "android";
