"""One adapter per image family: how to read the delivered PNG and solve the task from it.

Each adapter exposes
    read(img, layout)                       -> state read from pixels (raises on failure)
    describe(state)                         -> one line for the check report
    data_checks(data, state)                -> [(name, ok, detail)] comparing pixels with the stored data
    answer(state, program, parsed, data)    -> the answer computed from the pixel reading
None of them imports a generator's solver.
"""
from __future__ import annotations

from . import traingraph_read


class TrainGraph:
    read = staticmethod(traingraph_read.read)
    describe = staticmethod(traingraph_read.describe)

    @staticmethod
    def data_checks(data, state):
        return [("timetable_matches_data", *traingraph_read.matches_data(data, state))]

    @staticmethod
    def answer(state, program, parsed, data):
        return traingraph_read.answer(state, program, parsed, data)


FAMILIES = {"traingraph": TrainGraph}
