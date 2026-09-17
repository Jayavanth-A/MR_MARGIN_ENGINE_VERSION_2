from pathlib import Path
import pytest

from engine.models import ImageRecord, TimelineEntry, TimelineReconciliationError
from engine.reconciler import TimelineReconciler


def test_reconciler_safe_micro_reconciliation():
    images = [
        ImageRecord(index=1, filename="1.jpg", path=Path("1.jpg"), prompt="p1"),
        ImageRecord(index=2, filename="2.jpg", path=Path("2.jpg"), prompt="p2"),
        ImageRecord(index=3, filename="3.jpg", path=Path("3.jpg"), prompt="p3"),
    ]

    # Raw entries with tiny sub-frame floating point drifts (e.g. 0.04s)
    raw_entries = [
        TimelineEntry(image_index=1, filename="1.jpg", start=0.0, end=2.04),
        TimelineEntry(image_index=2, filename="2.jpg", start=2.00, end=4.52),
        TimelineEntry(image_index=3, filename="3.jpg", start=4.50, end=6.00),
    ]

    target_duration = 6.000
    timeline = TimelineReconciler.reconcile(raw_entries, images, target_duration)

    assert timeline.image_count == 3
    assert timeline.audio_duration == target_duration

    entries = timeline.timeline
    # Check start and end
    assert entries[0].start == 0.000
    assert pytest.approx(entries[-1].end, abs=1e-3) == target_duration

    # Check continuity: no gaps, no overlaps
    for i in range(len(entries) - 1):
        assert entries[i].end == entries[i + 1].start
        assert entries[i].duration > 0

    # Image order must be strictly 1, 2, 3
    assert [e.image_index for e in entries] == [1, 2, 3]


def test_reconciler_rejects_major_gap():
    images = [
        ImageRecord(index=1, filename="1.jpg", path=Path("1.jpg"), prompt="p1"),
        ImageRecord(index=2, filename="2.jpg", path=Path("2.jpg"), prompt="p2"),
    ]

    # Major gap of 5.0 seconds between image 1 and image 2
    raw_entries = [
        TimelineEntry(image_index=1, filename="1.jpg", start=0.0, end=5.0),
        TimelineEntry(image_index=2, filename="2.jpg", start=10.0, end=15.0),
    ]

    with pytest.raises(TimelineReconciliationError) as excinfo:
        TimelineReconciler.reconcile(raw_entries, images, 15.0)

    assert "Major gap detected" in str(excinfo.value)
    assert "Major timing discrepancies cannot be silently repaired" in str(excinfo.value)


def test_reconciler_rejects_major_overlap():
    images = [
        ImageRecord(index=1, filename="1.jpg", path=Path("1.jpg"), prompt="p1"),
        ImageRecord(index=2, filename="2.jpg", path=Path("2.jpg"), prompt="p2"),
    ]

    # Major overlap of 5.0 seconds
    raw_entries = [
        TimelineEntry(image_index=1, filename="1.jpg", start=0.0, end=10.0),
        TimelineEntry(image_index=2, filename="2.jpg", start=5.0, end=15.0),
    ]

    with pytest.raises(TimelineReconciliationError) as excinfo:
        TimelineReconciler.reconcile(raw_entries, images, 15.0)

    assert "Major overlap detected" in str(excinfo.value)


def test_reconciler_rejects_missing_image():
    images = [
        ImageRecord(index=1, filename="1.jpg", path=Path("1.jpg"), prompt="p1"),
        ImageRecord(index=2, filename="2.jpg", path=Path("2.jpg"), prompt="p2"),
    ]

    # Image 2 is missing from raw entries
    raw_entries = [
        TimelineEntry(image_index=1, filename="1.jpg", start=0.0, end=5.0),
    ]

    with pytest.raises(TimelineReconciliationError) as excinfo:
        TimelineReconciler.reconcile(raw_entries, images, 5.0)

    assert "Missing image indexes in timeline" in str(excinfo.value)
    assert "2" in str(excinfo.value)
