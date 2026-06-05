from pygments.token import Comment, Keyword, Name, Number

from tcl_lathe_hmi.gcode import LinuxCncGCodeLexer


def _tokens(text: str):
    return [(token, value) for token, value in LinuxCncGCodeLexer().get_tokens(text) if value.strip()]


def test_gcode_lexer_highlights_motion_words_and_comments():
    tokens = _tokens("N10 G1 X1.5 Z-2.0 F100 ; feed move\n")

    assert (Name.Label, "N") in tokens
    assert (Number.Integer, "10") in tokens
    assert (Keyword, "G") in tokens
    assert (Number.Float, "1") in tokens
    assert (Name.Variable, "X") in tokens
    assert (Number.Float, "1.5") in tokens
    assert (Comment.Single, "; feed move") in tokens


def test_gcode_lexer_highlights_linuxcnc_o_words_and_parameters():
    tokens = _tokens("O<turn> sub\n#<depth> = -2.5\n(O-word body)\nO<turn> endsub\n")

    assert (Keyword.Declaration, "O") in tokens
    assert (Name.Label, "<turn>") in tokens
    assert (Keyword.Reserved, "sub") in tokens
    assert (Name.Variable, "<depth>") in tokens
    assert (Number.Float, "-2.5") in tokens
    assert (Comment.Single, "(O-word body)") in tokens
    assert (Keyword.Reserved, "endsub") in tokens
