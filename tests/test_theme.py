from kdbmonitor.core import theme


def test_categorical_colours_cycle():
    n = len(theme.CATEGORICAL)
    assert theme.color_for(0) == theme.CATEGORICAL[0]
    assert theme.color_for(n) == theme.CATEGORICAL[0]      # wraps
    assert theme.color_for(1) != theme.color_for(0)


def test_every_colour_is_a_hex_string():
    for c in theme.CATEGORICAL + list(theme.SEMANTIC.values()):
        assert c.startswith("#") and len(c) == 7


def test_semantic_names_cover_the_widget_vocabulary():
    assert set(theme.SEMANTIC) >= {"ink", "muted", "good", "critical", "blue"}


def test_resolve_colour_accepts_names_and_literals():
    assert theme.resolve_color("good") == theme.GOOD
    assert theme.resolve_color("#123456") == "#123456"
    assert theme.resolve_color(None) == theme.INK


def test_applying_the_theme_is_idempotent():
    theme.apply_seaborn_theme()
    theme.apply_seaborn_theme()
    import matplotlib.pyplot as plt
    assert plt.rcParams["figure.facecolor"] == theme.SURFACE
