import React, { useState } from "react";

export function Avatar({
  account,
  peer,
  name,
  photoId,
  className = "",
}: {
  account: number;
  peer: string;
  name: string;
  photoId?: string | null;
  className?: string;
}) {
  const url = photoId
    ? `/api/accounts/${account}/avatars/${encodeURIComponent(peer)}?photo_id=${encodeURIComponent(photoId)}`
    : null;
  const [failed, setFailed] = useState<string | null>(null);
  const initials = name
    .trim()
    .split(/\s+/)
    .filter((part) => /[\p{L}\p{N}]/u.test(part))
    .slice(0, 2)
    .map((part) => part.match(/[\p{L}\p{N}]/u)?.[0] || "")
    .join("")
    .toUpperCase();
  return (
    <span
      className={`profile-avatar ${className}`}
      role="img"
      aria-label={`${name} avatar`}
    >
      {url && failed !== url ? (
        <img
          key={url}
          src={url}
          alt=""
          loading="lazy"
          onError={() => setFailed(url)}
        />
      ) : (
        <span aria-hidden="true">{initials || "?"}</span>
      )}
    </span>
  );
}
