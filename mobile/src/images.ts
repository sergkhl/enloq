import { ImageManipulator, SaveFormat } from "expo-image-manipulator";
import * as ImagePicker from "expo-image-picker";

// The service's photo bounds (`transport/media.py`): 5 MiB and 4096 px a
// side; ten images make one Telegram album.
const MAX_BYTES = 5 * 1024 * 1024;
const MAX_SIDE = 4096;
export const MAX_IMAGES = 10;
const QUALITIES = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4];

export type Prepared = { name: string; data: string };

// Photos the owner picks, or one they take. Null when they cancel; a refused
// camera permission is an error with its reason.
export async function pickPhotos(
  source: "library" | "camera",
  limit: number,
): Promise<ImagePicker.ImagePickerAsset[] | null> {
  if (source === "camera") {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted)
      throw new Error(
        "Camera access is off for Enloq. Allow it in Settings to take photos here.",
      );
  }
  const options: ImagePicker.ImagePickerOptions = {
    mediaTypes: ["images"],
    quality: 1,
    exif: false,
  };
  const result =
    source === "camera"
      ? await ImagePicker.launchCameraAsync(options)
      : await ImagePicker.launchImageLibraryAsync({
          ...options,
          allowsMultipleSelection: limit > 1,
          selectionLimit: limit,
        });
  return result.canceled ? null : result.assets.slice(0, limit);
}

function jpegName(name: string | null | undefined, index: number) {
  const stem = (name ?? "").replace(/\.[^.]*$/, "").trim();
  return `${stem || `photo-${index + 1}`}.jpg`;
}

// One photo as a JPEG within the service's bounds: the longest side at most
// 4096 px, re-encoded at falling quality until it fits 5 MiB. Re-encoding
// also turns HEIC photos into a format the service accepts.
export async function preparePhoto(
  asset: Pick<
    ImagePicker.ImagePickerAsset,
    "uri" | "width" | "height" | "fileName"
  >,
  index: number,
): Promise<Prepared> {
  let { width, height } = asset;
  if (!width || !height) {
    const probe = await ImageManipulator.manipulate(asset.uri).renderAsync();
    ({ width, height } = probe);
  }
  const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
  const context = ImageManipulator.manipulate(asset.uri);
  if (scale < 1)
    context.resize(
      width >= height
        ? { width: Math.floor(width * scale) }
        : { height: Math.floor(height * scale) },
    );
  const image = await context.renderAsync();
  for (const compress of QUALITIES) {
    const saved = await image.saveAsync({
      format: SaveFormat.JPEG,
      compress,
      base64: true,
    });
    if (saved.base64 && (saved.base64.length * 3) / 4 <= MAX_BYTES)
      return { name: jpegName(asset.fileName, index), data: saved.base64 };
  }
  throw new Error("This photo stays over 5 MB even when compressed.");
}
