import Ionicons from "@expo/vector-icons/Ionicons";
import React from "react";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";
import { albumLayout } from "../../shared/gallery";
import { useBubbleMenu } from "./bubbleMenu";
import { useConnection } from "./connection";
import type { NativeMessage } from "./conversation";
import { useGallery } from "./gallery";
import { mediaSource } from "./Media";
import type { Palette } from "./theme";

// A Telegram album (ADR 51 Q12): its photos and videos as one grid laid out
// as Telegram does, each opening the Media Carousel there, then its caption.
export function AlbumGrid({
  album,
  account,
  peer,
  palette,
  inverse,
}: {
  album: NativeMessage[];
  account: number;
  peer: string;
  palette: Palette;
  inverse: boolean;
}) {
  const { connection } = useConnection();
  const gallery = useGallery();
  const menu = useBubbleMenu();
  if (connection.status !== "paired") return null;
  const caption = album.find((item) => item.text);
  let next = 0;
  return (
    <View style={styles.block}>
      <View
        accessibilityLabel={`Album, ${album.length} items`}
        style={styles.grid}
      >
        {albumLayout(album.length).map((size, row) => (
          <View
            key={row}
            style={[styles.row, album.length <= 2 ? styles.tall : null]}
          >
            {album.slice(next, (next += size)).map((item) => (
              <Pressable
                key={item.message_id}
                accessibilityRole="imagebutton"
                accessibilityLabel={`${item.media === "video" ? "Video" : "Photo"}, opens full screen`}
                onPress={() => gallery?.open(item)}
                {...menu}
                style={[styles.item, { backgroundColor: palette.line }]}
              >
                <Image
                  // An array: React Native's Android Image sends headers
                  // only from one.
                  source={[
                    mediaSource(connection.credential, account, peer, item, {
                      variant: item.media === "video" ? "poster" : undefined,
                    }),
                  ]}
                  resizeMode="cover"
                  style={StyleSheet.absoluteFill}
                />
                {item.media === "video" ? (
                  <View style={styles.play}>
                    <Ionicons name="play" size={22} color="#fff" />
                  </View>
                ) : null}
              </Pressable>
            ))}
          </View>
        ))}
      </View>
      {caption ? (
        <Text
          style={[
            styles.caption,
            { color: inverse ? palette.onAccent : palette.text },
          ]}
        >
          {caption.text}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  block: { gap: 6 },
  grid: { width: 240, gap: 2, borderRadius: 10, overflow: "hidden" },
  row: { flexDirection: "row", gap: 2, height: 120 },
  tall: { height: 180 },
  item: { flex: 1, overflow: "hidden" },
  play: {
    position: "absolute",
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    alignItems: "center",
    justifyContent: "center",
  },
  caption: { fontSize: 16, lineHeight: 22 },
});
