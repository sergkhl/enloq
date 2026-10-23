import { createContext, useContext } from "react";
import type { NativeMessage } from "./conversation";

// The chat's Media Carousel (ADR 51 Q10), which a photo or video opens at
// itself; `MediaCarousel.tsx` provides it.
export const Gallery = createContext<{
  open: (message: NativeMessage) => void;
} | null>(null);

export function useGallery() {
  return useContext(Gallery);
}
