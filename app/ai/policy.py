from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PolicyResult:
    state: str
    alert: bool
    positive_count: int


class AlertPolicy:
    """Operational alert policy; classification remains the GRU's responsibility."""

    def __init__(
        self,
        threshold: float = 0.75,
        confirm_frames: int = 3,
        rearm_frames: int = 8,
        cooldown_seconds: float = 45.0,
    ) -> None:
        self.threshold = float(threshold)
        self.confirm_frames = int(confirm_frames)
        self.rearm_frames = int(rearm_frames)
        self.cooldown_seconds = float(cooldown_seconds)
        self.state = "normal"
        self.positive_count = 0
        self.normal_count = 0
        self.cooldown_until = 0.0

    def reset(self) -> None:
        self.state = "normal"
        self.positive_count = 0
        self.normal_count = 0
        self.cooldown_until = 0.0

    def update(self, event_probs: list[float], observation: str, now: float) -> PolicyResult:
        normal_prob, falling_prob, postfall_prob = event_probs
        positive = max(falling_prob, postfall_prob) >= self.threshold
        observable = observation in {"clear", "partial"}

        if self.state == "alerted":
            if now >= self.cooldown_until and normal_prob >= self.threshold and observable:
                self.normal_count += 1
                if self.normal_count >= self.rearm_frames:
                    self.reset()
            else:
                self.normal_count = 0
            return PolicyResult(self.state, False, self.positive_count)

        if positive and observable:
            self.positive_count += 1
            self.state = "suspected"
        else:
            self.positive_count = 0
            self.state = "normal" if observable else "unknown"

        if self.positive_count >= self.confirm_frames:
            self.state = "alerted"
            self.cooldown_until = now + self.cooldown_seconds
            return PolicyResult(self.state, True, self.positive_count)
        return PolicyResult(self.state, False, self.positive_count)
