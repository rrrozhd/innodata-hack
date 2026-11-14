"""Train graph family: graphic timetables (time-distance diagrams) of a railway line."""
from .programs import PROGRAMS
from .render import render

# Real material this family imitates. Recorded with every task.
REFERENCES = [
    "E.-J. Marey, 'La methode graphique' (1885): the Paris-Lyon graphic train schedule, reproduced on the cover "
    "of E. Tufte, 'The Visual Display of Quantitative Information' (1983)",
    "Graphic timetable (Bildfahrplan / time-distance diagram) views produced by railway planning tools such as "
    "OpenTrack and the open-source jTrainGraph, and the graphic timetables published per line by Swiss Federal "
    "Railways",
]

__all__ = ["PROGRAMS", "REFERENCES", "render"]
