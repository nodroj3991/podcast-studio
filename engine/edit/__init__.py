"""Editing stage: transcript JSON -> edit decisions -> EDL -> preview/full render.

Pipeline (each step writes to the project folder and is resumable):

    plan     transcript.json  -> edit/decisions.json   (silences, fillers, chapters, cameras)
    review   decisions.json   -> (human edits in the review screen)
    edl      decisions.json   -> edit/edl.json + edit/edl.txt + edit/edl.csv
    render   edl.json         -> renders/preview.mp4  or  renders/episode_1080p.mp4
"""
__version__ = "0.2.0"
