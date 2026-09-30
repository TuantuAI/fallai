from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn

from app.ai import EVENTS, POSTURES


class FallGRU(nn.Module):
    def __init__(
        self,
        input_dim: int = 92,
        hidden_dim: int = 64,
        layers: int = 2,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        recurrent_dropout = dropout if layers > 1 else 0.0
        self.gru = nn.GRU(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=layers,
            batch_first=True,
            dropout=recurrent_dropout,
            bidirectional=False,
        )
        self.norm = nn.LayerNorm(hidden_dim)
        self.posture_head = nn.Sequential(
            nn.Linear(hidden_dim, 32), nn.ReLU(), nn.Dropout(dropout), nn.Linear(32, 3)
        )
        self.event_head = nn.Sequential(
            nn.Linear(hidden_dim, 32), nn.ReLU(), nn.Dropout(dropout), nn.Linear(32, 3)
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        output, _ = self.gru(x)
        state = self.norm(output[:, -1])
        return self.posture_head(state), self.event_head(state)


class GRUEngine:
    def __init__(
        self,
        model_path: Path,
        input_dim: int = 92,
        hidden_dim: int = 64,
        layers: int = 2,
    ) -> None:
        self.model_path = Path(model_path)
        self.default_config = {
            "input_dim": input_dim,
            "hidden_dim": hidden_dim,
            "layers": layers,
            "dropout": 0.2,
        }
        self.model: FallGRU | None = None
        self.meta: dict[str, Any] = {}
        self.error: str | None = None

    @property
    def ready(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        if not self.model_path.exists():
            self.error = f"Chua co GRU checkpoint: {self.model_path}"
            return
        try:
            checkpoint = torch.load(self.model_path, map_location="cpu", weights_only=False)
            config = dict(self.default_config)
            config.update(checkpoint.get("model_config", {}))
            self.model = FallGRU(**config)
            self.model.load_state_dict(checkpoint["state_dict"])
            self.model.eval()
            self.meta = checkpoint.get("meta", {})
            self.error = None
        except Exception as exc:  # model files can fail for many environment reasons
            self.model = None
            self.error = f"Khong nap duoc GRU: {exc}"

    @torch.inference_mode()
    def predict(self, sequence: np.ndarray) -> dict[str, Any]:
        if self.model is None:
            raise RuntimeError(self.error or "GRU is not loaded")
        tensor = torch.from_numpy(np.asarray(sequence, dtype=np.float32))[None, ...]
        posture_logits, event_logits = self.model(tensor)
        posture_probs = torch.softmax(posture_logits, dim=1)[0].cpu().numpy()
        event_probs = torch.softmax(event_logits, dim=1)[0].cpu().numpy()
        posture_idx = int(posture_probs.argmax())
        event_idx = int(event_probs.argmax())
        return {
            "posture": POSTURES[posture_idx],
            "posture_score": float(posture_probs[posture_idx]),
            "posture_probs": posture_probs.tolist(),
            "event": EVENTS[event_idx],
            "event_score": float(event_probs[event_idx]),
            "event_probs": event_probs.tolist(),
        }
