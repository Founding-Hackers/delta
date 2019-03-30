#!/usr/bin/env python
# -*- coding: utf-8 -*-
# __BEGIN_LICENSE__
#  Copyright (c) 2009-2013, United States Government as represented by the
#  Administrator of the National Aeronautics and Space Administration. All
#  rights reserved.
#
#  The NGT platform is licensed under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance with the
#  License. You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# __END_LICENSE__

"""
Script to apply Top of Atmosphere correction to Landsat 5, 7, and 8 files.
"""
import sys, os
import argparse
import math
import functools

# TODO: Clean this up
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

# TODO: Make sure this goes everywhere!
if sys.version_info < (3, 0, 0):
    print('\nERROR: Must use Python version >= 3.0.')
    sys.exit(1)

from image_reader import *
from image_writer import *

#------------------------------------------------------------------------------


def apply_function_to_file(input_path, output_path, user_function, tile_size=(0,0)):
    """Apply the given function to the entire input image and write the
       result into the output path.  The function is applied to each tile of data.
    """

    # Open the input image and get information about it
    input_paths = [input_path]
    input_reader = MultiTiffFileReader()
    input_reader.load_images(input_paths)
    (num_cols, num_rows) = input_reader.image_size()
    no_data_val = input_reader.nodata_value()
    (block_size_in, num_blocks_in) = input_reader.get_block_info(band=1)
    input_metadata = input_reader.get_all_metadata()

    input_bounds = Rectangle(0, 0, width=num_cols, height=num_rows)

    X = 0 # Make indices easier to read
    Y = 1

    # Use the input tile size unless the user specified one.
    block_size_out = block_size_in
    if tile_size[X] > 0:
        block_size_out[X] = int(tile_size[X])
    if tile_size[Y] > 0:
        block_size_out[Y] = int(tile_size[Y])

    print('Using output tile size: ' + str(block_size_out))

    # Make a list of output ROIs
    num_blocks_out = (int(math.ceil(num_cols / block_size_out[X])),
                      int(math.ceil(num_rows / block_size_out[Y])))

    # Set up the output image
    writer = TiffWriter()
    writer.init_output_geotiff(output_path, num_rows, num_cols, no_data_val,
                               tile_width=block_size_out[X],
                               tile_height=block_size_out[Y],
                               metadata=input_metadata,
                               data_type='float') # TODO: data type option?

    # Setting up output ROIs
    output_rois = []
    for r in range(0,num_blocks_out[Y]):
        for c in range(0,num_blocks_out[X]):

            # Get the ROI for the block, cropped to fit the image size.
            roi = Rectangle(c*block_size_out[X], r*block_size_out[Y],
                            width=block_size_out[X], height=block_size_out[Y])
            roi = roi.get_intersection(input_bounds)
            output_rois.append(roi)
            
            

    # TODO: Perform this processing in multiple threads!
    def callback_function(output_roi, read_roi, data_vec):
        """Callback function to write the first channel to the output file."""

        # Figure out the output block
        col = output_roi.min_x / block_size_out[X]
        row = output_roi.min_y / block_size_out[Y]

        data = data_vec[0] # TODO: Handle muliple channels?

        # Figure out where the desired output data falls in read_roi
        x0 = output_roi.min_x - read_roi.min_x
        y0 = output_roi.min_y - read_roi.min_y
        x1 = x0 + output_roi.width()
        y1 = y0 + output_roi.height()

        # Crop the desired data portion and apply the user function.
        output_data = user_function(data[y0:y1, x0:x1])
        
        #print(data[y0:y1, x0:x1][0][0])
        #print(output_data[0][0])

        # Write out the result
        writer.write_geotiff_block(output_data, col, row)

    print('Writing TIFF blocks...')
    input_reader.process_rois(output_rois, callback_function)


    print('Done sending in blocks!')
    writer.finish_writing_geotiff()
    print('Done duplicating the image!')

    time.sleep(2)
    print('Cleaning up the writer!')
    writer.cleanup()

    image = None # Close the image


def allocate_bands_for_spacecraft(landsat_number):

    BAND_COUNTS = {'5':7, '7':8, '8':11}

    num_bands = BAND_COUNTS[landsat_number]
    data = dict()

    # There are fewer K constants but we store in the the
    # appropriate band indices.
    data['FILE_NAME'       ] = [''] * num_bands
    data['RADIANCE_MULT'   ] = [1] * num_bands
    data['RADIANCE_ADD'    ] = [0] * num_bands
    data['REFLECTANCE_MULT'] = [1] * num_bands
    data['REFLECTANCE_ADD' ] = [0] * num_bands
    data['K1_CONSTANT'     ] = [1] * num_bands
    data['K2_CONSTANT'     ] = [1] * num_bands

    return data

def parse_mtl_file(mtl_path):
    """Parse out the needed values from the MTL file"""

    if not os.path.exists(mtl_path):
        raise Exception('MTL file not found: ' + mtl_path)

    # These are all the values we want to read in
    DESIRED_TAGS = ['FILE_NAME', 'RADIANCE_MULT', 'RADIANCE_ADD',
                    'REFLECTANCE_MULT', 'REFLECTANCE_ADD',
                    'K1_CONSTANT', 'K2_CONSTANT']

    data = None
    with open(mtl_path, 'r') as f:
        for line in f:

            line = line.replace('"','') # Clean up

            # Get the spacecraft ID and allocate storage
            if 'SPACECRAFT_ID = LANDSAT_' in line:
                spacecraft_id = line.split('_')[-1].strip()
                data = allocate_bands_for_spacecraft(spacecraft_id)

            # Look for the other info we want
            for tag in DESIRED_TAGS:
                t = tag + '_BAND'
                if t in line: # TODO: Better to do regex here

                    # Break out the name, value, and band
                    parts = line.split('=')
                    name  = parts[0].strip()
                    value = parts[1].strip()
                    try:
                        band  = int(name.split('_')[-1]) -1 # One-based to zero-based
                    except ValueError: # Means this is not a proper match
                        break

                    if tag == 'FILE_NAME':
                        data[tag][band] = value # String
                    else:
                        data[tag][band] = float(value)
    
    #print('Read MTL data')
    #print(data)
    
    return data


def apply_toa(data, factor, constant):
    """Apply a top of atmosphere conversion to landsat data"""
    return (data * factor) + constant



def main(argsIn):

    try:

        # Use parser that ignores unknown options
        usage  = "usage: landsat_toa [options]"
        parser = argparse.ArgumentParser(usage=usage)

        #parser.add_argument('input_paths', metavar='N', type=str, nargs='+',
        #                    help='Input files')

        parser.add_argument("--mtl-path", dest="mtl_path", required=True,
                            help="Path to the MTL file in the same folder as the image band files.")

        parser.add_argument("--output-folder", dest="output_folder", required=True,
                            help="Write output band files to this output folder with the same names.")

        parser.add_argument("--tile-size", nargs=2, metavar=('tile_width', 'tile_height'),
                            dest='tile_size', default=[0,0], type=int,
                            help="Specify the output tile size.  Default is to keep the input tile size.")

        # This call handles all the parallel_mapproject specific options.
        options = parser.parse_args(argsIn)

        # Check the required positional arguments.

    except argparse.ArgumentError as msg:
        raise Usage(msg)

    if not os.path.exists(options.output_folder):
        os.mkdir(options.output_folder)

    # TODO: Process all bands simultaneously?
    # TODO: Allow radiance and reflectance options!

    # Get all of the TOA coefficients and input file names
    data = parse_mtl_file(options.mtl_path)

    # Loop through the input files
    input_folder = os.path.dirname(options.mtl_path)
    num_bands    = len(data['FILE_NAME'])
    for band in range(0, num_bands):
      
        print('Processing band: ' + str(band))
      
        fname = data['FILE_NAME'][band]
        
        input_path  = os.path.join(input_folder,  fname)
        output_path = os.path.join(options.output_folder, fname)
        
        #print(input_path)
        #print(output_path)
        
        rad_mult = data['RADIANCE_MULT'   ][band]
        rad_add  = data['RADIANCE_ADD'    ][band]
        ref_mult = data['REFLECTANCE_MULT'][band]
        ref_add  = data['REFLECTANCE_ADD' ][band]
        k1_const = data['K1_CONSTANT'][band]
        k2_const = data['K2_CONSTANT'][band]
        
        #print(rad_mult)
        #print(rad_add)
        
        # TODO: Not every band uses a K value!

        user_function = functools.partial(apply_toa, factor=rad_mult, constant=rad_add)
        apply_function_to_file(input_path, output_path, user_function, options.tile_size)
          
        #raise Exception('DEBUG')
    
    print('Landsat TOA conversion is finished.')

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
    
    
    
