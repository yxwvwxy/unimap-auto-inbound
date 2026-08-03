from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

TARGET_STATUS = 215


@dataclass
class TransitionStep:
    from_statuses: List[int]
    next_transition: str
    to_status: int
    to_label: str
    extras: List[str] = field(default_factory=list)


TRANSITION_STEPS: List[TransitionStep] = [
    # 190 / 255 -> gateway processing -> 199
    TransitionStep([190, 255], "gateway processing", 199, "gateway transit"),
    # 199 / 195 / 1910 -> parcel scan -> 200
    TransitionStep([199, 195, 1910], "parcel scan", 200, "parcel scanned"),
    # Mid-path nodes: resume from whichever status the parcel is already in
    TransitionStep([200], "wrong address cfm in dispatch", 212, "wrong address from receive"),
    TransitionStep(
        [212],
        "deliver parcel apt",
        211,
        "return office from transit",
        extras=["failReason"],
    ),
    TransitionStep(
        [211],
        "send parcel to storage",
        213,
        "storage 30 days from office",
        extras=["warehouse"],
    ),
    TransitionStep([213], "parcel abandon", 215, "parcel abandoned"),
]

# UI may show spaces, underscores, or ALL_CAPS like Tracking Info (190: ORDER_RECEIVED)
# UniUni dropdown values are usually ALL_CAPS_WITH_UNDERSCORES (see UI).
TRANSITION_ALIASES = {
    "gateway processing": [
        "GATEWAY_PROCESSING",
        "gateway processing",
        "gateway proccessing",
        "GATEWAY PROCESSING",
    ],
    "parcel scan": [
        "PARCEL_SCAN",
        "PARCEL_SCANNED",
        "parcel scan",
        "parcel scanned",
        "PARCEL SCAN",
        "PARCEL SCANNED",
    ],
    "wrong address cfm in dispatch": [
        "WRONG_ADDRESS_CFM_IN_DISPATCH",
        "wrong address cfm in dispatch",
        "wrong address confirm in dispatch",
        "WRONG ADDRESS CFM IN DISPATCH",
    ],
    "deliver parcel apt": [
        "DELIVER_PARCEL_APT",
        "deliver parcel apt",
        "DELIVER PARCEL APT",
    ],
    "send parcel to storage": [
        "SEND_PARCEL_TO_STORAGE",
        "send parcel to storage",
        "SEND PARCEL TO STORAGE",
    ],
    "parcel abandon": [
        "PARCEL_ABANDON",
        "PARCEL_ABANDONED",
        "parcel abandon",
        "parcel abandoned",
        "PARCEL ABANDON",
        "PARCEL ABANDONED",
    ],
}

# Only statuses explained by the user are allowed. 190 IS included.
KNOWN_STATUSES: Set[int] = {215}
for _step in TRANSITION_STEPS:
    KNOWN_STATUSES.update(_step.from_statuses)
    KNOWN_STATUSES.add(_step.to_status)


class PathMismatchError(RuntimeError):
    """Unknown status or transition result does not match the explained path."""

    def __init__(self, message: str):
        super().__init__(message)
        self.stop_run = True


def is_known_status(status: int) -> bool:
    return status in KNOWN_STATUSES


def find_step_for_status(status: int) -> Optional[TransitionStep]:
    if status == TARGET_STATUS:
        return None
    for step in TRANSITION_STEPS:
        if status in step.from_statuses:
            return step
    return None


def require_known_status(status: Optional[int], context: str = "") -> int:
    prefix = f"{context}: " if context else ""
    if status is None:
        raise PathMismatchError(
            f"{prefix}无法从页面识别当前状态，已停止。"
            f"请确认 Tracking Info 里能看到如 190: ORDER_RECEIVED。"
            f"已知状态: {sorted(KNOWN_STATUSES)}"
        )
    if not is_known_status(status):
        raise PathMismatchError(
            f"{prefix}遇到未讲解过的状态 {status}，已停止。"
            f"已知状态: {sorted(KNOWN_STATUSES)}"
        )
    return status
