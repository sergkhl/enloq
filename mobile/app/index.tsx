import { Redirect } from "expo-router";
import { ActivityIndicator, View } from "react-native";
import { useConnection } from "../src/connection";
import { usePalette } from "../src/theme";

export default function Index() {
  const { connection } = useConnection();
  const palette = usePalette();
  if (connection.status === "loading")
    return (
      <View
        style={{
          flex: 1,
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: palette.background,
        }}
      >
        <ActivityIndicator color={palette.accent} />
      </View>
    );
  return (
    <Redirect
      href={
        connection.status === "paired"
          ? "/chats"
          : connection.status === "waiting"
            ? "/waiting"
            : "/pair"
      }
    />
  );
}
