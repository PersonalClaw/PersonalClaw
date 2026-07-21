"""Design-system token resolution (Design loop kind)."""

from personalclaw.loop import design_tokens as dt


def test_default_tokens_cover_every_axis():
    tree = dt.default_tokens()
    for axis in (
        "color",
        "typography",
        "spacing",
        "sizing",
        "radius",
        "border",
        "shadow",
        "elevation",
        "opacity",
        "blur",
        "motion",
        "zIndex",
        "breakpoint",
        "gradient",
        "component",
    ):
        assert axis in tree, f"default tokens missing axis {axis}"
    # color has primitive scales + semantic light/dark role maps
    assert "primitive" in tree["color"] and "semantic" in tree["color"]
    assert "light" in tree["color"]["semantic"] and "dark" in tree["color"]["semantic"]


def test_schema_loads():
    assert dt.tokens_schema().get("title") == "PersonalClaw Design Tokens"


def test_deep_merge_is_pure_and_recursive():
    base = {"color": {"primitive": {"brand": {"500": "#aaa", "600": "#bbb"}}}}
    override = {"color": {"primitive": {"brand": {"500": "#fff"}}}}
    merged = dt.deep_merge(base, override)
    assert merged["color"]["primitive"]["brand"]["500"] == "#fff"
    assert merged["color"]["primitive"]["brand"]["600"] == "#bbb"  # untouched
    # inputs not mutated
    assert base["color"]["primitive"]["brand"]["500"] == "#aaa"


def test_override_cascades_through_role_and_gradient():
    r = dt.resolve({"color": {"primitive": {"brand": {"500": "#ff0000", "700": "#990000"}}}})
    # semantic role referencing the primitive resolves to the override
    assert r["color"]["semantic"]["light"]["brand.default"] == "#ff0000"
    # embedded refs inside a gradient string resolve too
    assert "#ff0000" in r["gradient"]["brand"] and "#990000" in r["gradient"]["brand"]


def test_component_tokens_keep_semantic_role_refs():
    # Component values point at roles (resolved per-scheme at render), not literals.
    r = dt.resolve({})
    assert r["component"]["button"]["primary"]["bg"] == "brand.default"


def test_css_variables_emit_prefixed_and_scheme_aware():
    css_light = dt.to_css_variables({}, scheme="light")
    css_dark = dt.to_css_variables({}, scheme="dark")
    assert ":root {" in css_light
    assert "--pc-radius-lg:" in css_light
    assert "--pc-color-fg-default:" in css_light

    # fg.default differs between schemes
    def _line(css, name):
        return next(ln for ln in css.splitlines() if name in ln)

    assert _line(css_light, "--pc-color-fg-default:") != _line(css_dark, "--pc-color-fg-default:")


def test_unresolvable_ref_left_literal_not_crash():
    r = dt.resolve({"color": {"semantic": {"light": {"bg.base": "{color.primitive.missing.999}"}}}})
    assert r["color"]["semantic"]["light"]["bg.base"] == "{color.primitive.missing.999}"


def _rem(v: str) -> float:
    return (
        float(v.replace("rem", ""))
        if v.endswith("rem")
        else (0.0 if v in ("0", "0px") else float("inf"))
    )


def test_partial_spacing_override_does_not_scramble_the_scale():
    # A planner authoring spacing on a 2px-per-index convention (1=2px, 4=8px, 6=12px…)
    # must NOT key-merge onto the default 4px-grid scale (1=0.25rem, 3=0.75rem…) — that
    # produced a non-monotonic scale live (step 3 > step 4; 3 == 6; 12 < 9). The override
    # REPLACES the scale wholesale: only the keys it sets survive, in its own convention.
    partial = {
        "spacing": {
            "1": "0.125rem",
            "2": "0.25rem",
            "4": "0.5rem",
            "6": "0.75rem",
            "8": "1rem",
            "12": "1.5rem",
            "16": "2rem",
        }
    }
    sp = dt.resolve(partial)["spacing"]
    # The default numeric steps the override didn't set are GONE (no collision possible).
    assert "3" not in sp and "5" not in sp and "9" not in sp
    # What remains is exactly the override's scale, and it's monotonic by numeric key.
    steps = sorted((float(k), _rem(v)) for k, v in sp.items() if _is_num(k))
    vals = [v for _, v in steps]
    assert vals == sorted(vals), f"spacing scale not monotonic: {steps}"
    # The default's non-value meta key is preserved.
    assert "comment" in sp


def test_full_spacing_override_is_honored_and_monotonic():
    full = {"spacing": {str(i): f"{i*0.25}rem" for i in range(0, 13)}}
    sp = dt.resolve(full)["spacing"]
    assert sp["4"] == "1.0rem" and sp["12"] == "3.0rem"


def test_partial_typography_size_override_does_not_scramble_the_named_scale():
    # typography.size is a magnitude scale with T-SHIRT keys (xs/sm/…/3xl), not numeric.
    # A planner redefined the low+mid steps (raising lg/xl/2xl, adding md/glyph) but omitted
    # the high steps — live, the default 3xl=1.875rem survived next to the override 2xl=2.25rem
    # → 2xl > 3xl, a non-monotonic scale. The wholesale-replace must apply to named-key scales
    # too (the value-based step detector), so no stale default step survives to collide.
    partial = {
        "typography": {
            "size": {
                "xs": "0.75rem",
                "sm": "0.875rem",
                "base": "1rem",
                "md": "1.125rem",
                "lg": "1.375rem",
                "xl": "1.75rem",
                "2xl": "2.25rem",
                "glyph": "3.5rem",
            }
        }
    }
    size = dt.resolve(partial)["typography"]["size"]
    # The default high steps the override didn't set are GONE (no 2xl>3xl collision).
    assert "3xl" not in size and "4xl" not in size and "8xl" not in size
    # The surviving named scale is monotonic in rem order (excluding the meta comment).
    order = ["xs", "sm", "base", "md", "lg", "xl", "2xl", "glyph"]
    vals = [_rem(size[k]) for k in order if k in size]
    assert vals == sorted(
        vals
    ), f"type size scale not monotonic: {[(k, size.get(k)) for k in order]}"
    # The default's prose meta key is preserved (value-based detector keeps non-dimensions).
    assert "comment" in size


def test_is_scale_step_is_value_based():
    # Dimensions + bare numbers are steps; prose/meta is not — so meta keys survive.
    assert dt._is_scale_step("1.5rem") and dt._is_scale_step("24px") and dt._is_scale_step("0")
    assert dt._is_scale_step(8) and dt._is_scale_step(0.5)
    assert not dt._is_scale_step("Modular scale (~1.2 minor third) in rem.")
    assert not dt._is_scale_step("") and not dt._is_scale_step(None)


def test_color_leaf_override_still_key_merges():
    # Leaf families are NOT scales — "set only what you change" must still work: an
    # override of brand.500 keeps the rest of the brand ramp from the defaults.
    r = dt.resolve({"color": {"primitive": {"brand": {"500": "#123456"}}}})
    brand = r["color"]["primitive"]["brand"]
    assert brand["500"] == "#123456"
    assert len([k for k in brand if k.isdigit()]) > 3  # other steps survived


def _is_num(k: str) -> bool:
    try:
        float(k)
        return True
    except ValueError:
        return False
