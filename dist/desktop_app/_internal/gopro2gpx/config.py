#
# 17/02/2019 
# Juan M. Casillas <juanm.casillas@gmail.com>
# https://github.com/juanmcasillas/gopro2gpx.git
#
# Released under GNU GENERAL PUBLIC LICENSE v3. (Use at your own risk)
#

import os
import platform
import sys


class Config(object):
    def __init__(self, input_file, outputfile, format, verbose, skip):
        self.ffmpeg_cmd = os.getenv("FFMPEG_PATH", "ffmpeg")
        self.ffprobe_cmd = os.getenv("FFPROBE_PATH", "ffprobe")
        self.verbose = verbose
        self.format = format
        self.input_file = input_file
        self.output_file = outputfile
        self.skip = skip
