from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _template(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_driver_vehicle_page_uses_global_flash_renderer_only():
    template = _template("templates/driver/car_detail.html")

    assert "get_flashed_messages" not in template


def test_mileage_history_uses_semantic_observation_badges():
    timeline = _template("templates/reports/timeline.html")
    update_odometer = _template("templates/admin/update_odometer.html")

    for template in (timeline, update_odometer):
        assert "Advisor observation" in template
        assert "Accepted observation" in template
        assert "Submitted for review" in template
        assert "Not accepted" in template
        assert "Current observation" not in template


def test_care_signal_timeline_has_its_own_semantic_category():
    template = _template("templates/reports/timeline.html")

    assert '"Care signal" if is_care_signal else item.type' in template
    assert "if not is_care_signal" in template


def test_update_odometer_heading_does_not_append_year_twice():
    template = _template("templates/admin/update_odometer.html")

    assert "{{ car.display_name }}" in template
    assert "{{ car.decoded_display_name }} {{ car.year }}" not in template
