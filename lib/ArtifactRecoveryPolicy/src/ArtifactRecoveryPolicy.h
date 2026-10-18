#pragma once

namespace rgbbill::storage {

enum class ArtifactBootState {
  kEmpty,
  kReady,
  kRecoveryBlocked,
};

// Empty is safe only when no durable or partial artifact file exists. A
// payload/sidecar mismatch and an interrupted .part file are both explicit
// recovery boundaries; neither may be silently discarded or reported empty.
inline ArtifactBootState classifyArtifactBootState(
    bool hasPart, bool hasReady, bool hasMetadata, bool metadataValid) {
  if (!hasPart && !hasReady && !hasMetadata) {
    return ArtifactBootState::kEmpty;
  }
  if (!hasPart && hasReady && hasMetadata && metadataValid) {
    return ArtifactBootState::kReady;
  }
  return ArtifactBootState::kRecoveryBlocked;
}

}  // namespace rgbbill::storage
