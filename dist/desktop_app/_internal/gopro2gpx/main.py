import sys

import click

from .gopro2gpx import BuildGPSPoints
from .gpshelper import generate_KML, generate_GPX
from .gpmf import Parser
from .config import Config


@click.command()
@click.option("-v", "--verbose", help="increase output verbosity")
@click.option("-b", "--binary", help="read data from bin file")
@click.option("-s", "--skip", help="Skip bad points (GPSFIX=0)")
@click.option("-i", "--input_file", help="Video file or binary metadata dump")
@click.option("-o", "--output_file", help="Output file")
@click.option("-f", "--format", help="", type=click.Choice(['gpx', 'kml'], case_sensitive=False))
def extract_main(input_file, output_file, format, verbose, binary, skip):
    return extract(input_file, output_file, format, verbose, binary, skip)


def extract(input_file, output_file=None, format="GPX", verbose=1, binary=False, skip=True):
    config = Config(input_file=input_file,
                    outputfile=output_file,
                    format=format,
                    verbose=verbose,
                    skip=skip)
    parser = Parser(config)

    if not binary:
        data = parser.readFromMP4()
    else:
        data = parser.readFromBinary()

    points = BuildGPSPoints(data, skip=config.skip)

    if len(points) == 0:
        print("Can't create file. No GPS info in %s. Exitting" % input_file)
        sys.exit(0)

    if config.format == "KML":
        kml = generate_KML(points)
        if config.output_file:
            with open("%s.kml" % config.output_file, "w+") as fd:
                fd.write(kml)
        else:
            return kml

    elif config.format == "GPX":
        gpx = generate_GPX(points, trk_name="gopro7-track")
        if config.output_file:
            with open("%s.gpx" % config.output_file, "w+") as fd:
                fd.write(gpx)
        else:
            return gpx

if __name__ == '__main__':
    result = extract_main()
    print(result)
