from __future__ import annotations

from pygments.lexer import RegexLexer, bygroups
from pygments.style import Style
from pygments.token import Comment, Error, Keyword, Name, Number, Operator, Text


class LinuxCncGCodeLexer(RegexLexer):
    name = "LinuxCNC G-code"
    aliases = ["linuxcnc-gcode", "gcode", "ngc"]
    filenames = ["*.ngc", "*.gcode", "*.nc", "*.tap"]
    flags = 0

    _number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    _word_letters = r"XYZABCUVWIJKRFPQDLHST"

    tokens = {
        "root": [
            (r"\s+", Text),
            (r";[^\n]*", Comment.Single),
            (r"\([^()\n]*\)", Comment.Single),
            (
                r"(?i)(O)(\s*)(<[^>\n]+>|\d+)(\s+)(sub|endsub|call|if|elseif|else|endif|while|endwhile|do|repeat|endrepeat|return|break|continue)\b",
                bygroups(Keyword.Declaration, Text, Name.Label, Text, Keyword.Reserved),
            ),
            (
                r"(?i)(N)(\s*)([+-]?\d+)\b",
                bygroups(Name.Label, Text, Number.Integer),
            ),
            (
                rf"(?i)([GM])(\s*)({_number})\b",
                bygroups(Keyword, Text, Number.Float),
            ),
            (
                rf"(?i)([{_word_letters}])(\s*)({_number})\b",
                bygroups(Name.Variable, Text, Number.Float),
            ),
            (
                r"(?i)(#)(\s*)(<[^>\n]+>|[+-]?\d+)",
                bygroups(Operator, Text, Name.Variable),
            ),
            (rf"{_number}\b", Number.Float),
            (r"[%\[\]=+\-*/]", Operator),
            (r"<[^>\n]+>", Name.Variable),
            (r"[A-Za-z_][A-Za-z0-9_]*", Text),
            (r".", Error),
        ]
    }


class TclGCodeStyle(Style):
    background_color = "#0d0f10"
    default_style = ""
    styles = {
        Text: "#e7ecef",
        Comment: "#8a949c italic",
        Keyword: "#7cc6ff bold",
        Keyword.Declaration: "#ffd166 bold",
        Keyword.Reserved: "#ffd166",
        Name.Label: "#b8f2e6",
        Name.Variable: "#f4a261 bold",
        Number: "#c4f1be",
        Operator: "#d6dee3",
        Error: "#ff7b7b",
    }
