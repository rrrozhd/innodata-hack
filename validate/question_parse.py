"""Read the task back out of the question text.

The independent solver takes its instructions from the same words the model
sees, not from the generator's structured data. If the wording cannot be parsed
the task is rejected.
"""
from __future__ import annotations

import re


class ParseError(Exception):
    pass


def _one(pattern: str, text: str, what: str):
    found = re.findall(pattern, text)
    if len(found) != 1:
        raise ParseError(f"question does not state {what} exactly once (found {len(found)})")
    return found[0]


def _chart_rules(question: str) -> None:
    _one(r"A line that slopes down to the right is a train running from", question, "the direction rule")
    _one(r"A horizontal stretch on a station line is the train standing at that station", question,
         "the standing rule")
    _one(r"A train's number is printed where its line starts", question, "where train numbers are printed")


def tg_overtakes(question: str) -> dict:
    _chart_rules(question)
    _one(r"A train is overtaken when it stands at a station while a train running in the same direction passes it",
         question, "what overtaking means")
    return {"train": _one(r"Question: which trains overtake train (\d+)\?", question, "the train")}


def tg_meets(question: str) -> dict:
    _chart_rules(question)
    train = _one(r"Question: how many trains running in the opposite direction does train (\d+) meet", question,
                 "the train")
    _one(r"Count every train whose line crosses the line of train " + train + r", including trains it meets while "
         r"standing at a station", question, "the counting rule")
    return {"train": train}


def tg_section(question: str) -> dict:
    _chart_rules(question)
    a, b, time = _one(r"Question: which trains are between (.+?) and (.+?) at (\d\d:\d\d), in either direction\?",
                      question, "the stretch of line and the time")
    _one(r"but not a train that is standing at " + re.escape(a) + " or at " + re.escape(b) + " at that moment",
         question, "the rule for the two end stations")
    return {"a": a, "b": b, "time": time}


def tg_nth(question: str) -> dict:
    _chart_rules(question)
    _one(r"A train arrives at a station at the moment its line first reaches that station's line", question,
         "what arriving means")
    ordinal, station, time = _one(r"which train is the (\w+) to arrive at (.+?) after (\d\d:\d\d)\?", question,
                                  "the station, the time and the position")
    return {"ordinal": ordinal, "station": station, "time": time}


def tg_longest(question: str) -> dict:
    _chart_rules(question)
    _one(r"Question: which train makes the longest single stop at a station on this chart\?", question,
         "the question")
    return {}


PARSERS = {"tg_overtakes": tg_overtakes, "tg_meets": tg_meets, "tg_section": tg_section, "tg_nth": tg_nth,
           "tg_longest": tg_longest}
