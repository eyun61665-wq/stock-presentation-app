from validators import catalyst_warnings, pl_warnings


def test_percent_bounds_warning():
    warnings = catalyst_warnings({"target_rate": 101, "capture_rate": -1, "evidence_category": "会社開示"})
    assert len(warnings) == 2


def test_unnatural_pl_values_warn():
    warnings = pl_warnings([{"年度": "2025/3", "売上高（百万円）": 100, "営業利益（百万円）": 120,
                             "純利益（百万円）": 130, "平均株式数（百万株）": 0}])
    assert len(warnings) == 3


def test_assumption_without_evidence_warns():
    warnings = catalyst_warnings({"target_rate": 50, "capture_rate": 10, "evidence_category": "自分の仮定", "source": "", "note": ""})
    assert any("補足" in warning for warning in warnings)


def test_sales_rate_model_warns_outside_percent_range():
    warnings = catalyst_warnings({
        "calculation_method": "売上比率モデル", "impact_rate": 120,
        "evidence_category": "会社開示",
    })
    assert any("売上影響率" in message for message in warnings)
