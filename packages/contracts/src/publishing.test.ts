import { describe, expect, it } from "vitest";

import { DestinationMetadata, publishMetadataHash } from "./publishing.js";

const metadata = [
  { caption: "Tiểu Vũ tu luyện", destination: "FACEBOOK", hashtags: ["#hoạthình", "#tu_tiên"] },
  { description: "Mô tả", destination: "YOUTUBE", tags: ["tiểu vũ", "tu luyện"], title: "Tiểu Vũ | Tập 3" },
] as const;

describe("publish metadata hash", () => {
  it("matches the processing agent's canonical metadata SHA-256", async () => {
    // Fixture computed by apps/processing-agent publishing.metadata.metadata_sha256 for the same entries.
    await expect(publishMetadataHash(metadata.map((entry) => DestinationMetadata.parse(entry)))).resolves.toBe(
      "b04b0990945e9d8081214dd065d5295ebe5f0e25b29eca44a9dbb18320497c26",
    );
  });

  it("does not depend on destination order", async () => {
    const parsed = metadata.map((entry) => DestinationMetadata.parse(entry));
    await expect(publishMetadataHash([...parsed].reverse())).resolves.toBe(await publishMetadataHash(parsed));
  });
});

describe("Facebook hashtags", () => {
  it("accept Vietnamese letters and reject punctuation or spaces", () => {
    const facebook = (hashtags: string[]) =>
      DestinationMetadata.safeParse({ caption: "caption", destination: "FACEBOOK", hashtags }).success;

    expect(facebook(["#hoạthình", "#tu_tiên", "#动画"])).toBe(true);
    expect(facebook(["#hoạt-hình"])).toBe(false);
    expect(facebook(["#a b"])).toBe(false);
  });
});
