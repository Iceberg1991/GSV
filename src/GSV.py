"""
 *******************************************************************************
 *                       Continental Confidential
 *                  Copyright (c) Continental AG. 2018
 *
 *      This software is furnished under license and may be used or
 *      copied only in accordance with the terms of such license.
 *******************************************************************************
 * @file    GSV.py
 * @brief
 *******************************************************************************
"""

import json
from lxml import etree
import math
import numpy as np
import os
from PIL import Image
import requests
from selenium import webdriver
import shutil
import ssl
import time

from RelativeTransform import RelativeTransform


class GSV(object):
    def __init__(self, coords, file_dir):
        self.site_api = "https://maps.googleapis.com/maps/api/streetview"
        self.metadata = "https://maps.googleapis.com/maps/api/streetview/metadata"
        self.key = "AIzaSyDqTW_9HS8vDobOBnTDDUxHZ2Sp6Crqiag"
        self.size = "size=640x640"
        self.header = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_2) '
                                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                                     'Chrome/55.0.2883.95 Safari/537.36'}
        self.proxies = {"http": "207.144.127.122:3128",
                        "https": "35.196.26.166:3128"}
        self.coords = coords
        self.ref_lon = coords[0, 0]
        self.ref_lat = coords[0, 1]
        self.relative_coords = self.get_relative_coords(coords[:, 0], coords[:, 1])
        self.remove_duplicate_coords()
        self.direction = self.get_direction()  # trajectory direction at each track point
        chrome = os.path.join(os.path.join(file_dir, "test"), "chromedriver")
        self.browser = webdriver.Chrome(chrome)
        self.get_images(file_dir)
        self.browser.quit()

    def get_relative_coords(self, lon_vec, lat_vec):
        rt = RelativeTransform(self.ref_lon, self.ref_lat)
        return rt.latlon_to_relative(lon_vec, lat_vec)

    @staticmethod
    def get_metadata_url(lon, lat, key):
        size = "size=640x640"
        return "?{}&location={},{}".format(size, lat, lon) \
               + "&fov=90&heading=0&pitch=0&key={}".format(key)

    def get_street_view_url(self, pano_id, heading, pitch, fov, width=None, height=None):
        if width is None or height is None:
            size = self.size
        else:
            if width > 640 or height > 640:
                raise Exception("Image size should be smaller than 640x640")
            size = "size=" + str(width) + "x" + str(height)
        return "?{}&pano={}".format(size, pano_id) \
               + "&fov={}".format(fov) \
               + "&heading={}&pitch={}".format(heading, pitch) \
               + "&key={}".format(self.key)

    def remove_duplicate_coords(self):
        idx = []
        for i in range(0, len(self.relative_coords) - 1):
            if np.linalg.norm(self.relative_coords[i + 1] - self.relative_coords[i]) < 0.1:
                idx.append(i + 1)
        if len(idx) == 0:
            return
        idx1 = np.ones(len(self.relative_coords), dtype=bool)
        idx1[idx] = False
        self.coords = self.coords[idx1]
        self.relative_coords = self.relative_coords[idx1]

    def get_direction(self):
        direction = np.zeros(len(self.relative_coords))
        for i in range(0, len(self.relative_coords) - 1):
            p = self.relative_coords[i + 1] - self.relative_coords[i]
            theta = np.arctan2(p[1], p[0])  # angle with local east direction
            if theta <= 0.5 * math.pi:
                phi = 0.5 * math.pi - theta  # angle with local north direction
            else:
                phi = 2 * math.pi - (theta - 0.5 * math.pi)
            direction[i] = phi
        direction[len(self.relative_coords) - 1] = direction[len(self.relative_coords) - 2]
        return direction

    def get_metadata(self):
        pano_ids = []
        pano_locations = []
        unique_id = set()
        for i in range(len(self.coords)):
            url = self.metadata + self.get_metadata_url(self.coords[i, 0],
                                                        self.coords[i, 1],
                                                        self.key)
            print("-- Get {}-th record's metadata.".format(i + 1))
            direction = self.direction[i]
            times = 0
            while times < 3:
                try:
                    time.sleep(0.5)
                    self.browser.get(url)
                except Exception as e:
                    print("Connection error:" + str(e) + " retry in 0.5 seconds...")
                times += 1
                page = self.browser.page_source
                content = json.loads(page[page.find('{'):page.rfind('}') + 1])
                tmp_id = content.get('pano_id')
                if tmp_id in unique_id:
                    break
                unique_id.add(tmp_id)
                pano_ids.append(tmp_id)
                pano_locations.append([content.get('location').get('lng'),
                                       content.get('location').get('lat'),
                                       direction])
                break
        return pano_ids, np.asarray(pano_locations)

    def get_images(self, out_dir):
        print("Get metadata...\n")
        ids, locations = self.get_metadata()
        print("Download images...\n")
        # rad to degree
        locations[:, 2] = locations[:, 2] * 180 / math.pi
        config = []
        pitch = 0
        out_dir = os.path.join(out_dir, 'images')
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
        for i in range(len(ids)):
            heading = np.array([locations[i, 2], locations[i, 2] + 90, locations[i, 2] + 180])
            heading[heading > 360] -= 360
            for j in range(3):
                pure_name = "gsv_{}_{}.png".format(i, j)
                img_file = os.path.join(out_dir, pure_name)
                if not os.path.exists(img_file):
                    url = self.site_api + self.get_street_view_url(ids[i], heading[j],
                                                                   pitch=pitch, fov=90)
                    times = 0
                    while times < 3:
                        try:
                            time.sleep(0.5)
                            self.browser.get(url)
                        except Exception as e:
                            print("Street View Downloading Error: " + str(e)
                                  + "\n Retry in 0.5 seconds...")
                            times += 1
                        # save screen shot of the returned image into local file
                        self.browser.save_screenshot(img_file)
                        # remove the black background of the image
                        img = self.browser.find_element_by_tag_name('img')
                        self.remove_background(img_file, img.location.get('x'), img.location.get('y'),
                                               img.size.get('width'), img.size.get('height'))
                        break
                config.append([pure_name, locations[i, 0], locations[i, 1], 0, heading[j], pitch, 0])
        self.write_config_file(config, os.path.join(out_dir, "config.txt"))

    @staticmethod
    def remove_background(in_file, x, y, width, height):
        im = Image.open(in_file)
        im = im.crop((x, y, x + width, y + height - 25))
        im.save(in_file)

    @staticmethod
    def write_config_file(data, file):
        lines = ""
        for p in data:
            lines += "{},{},{},{},{},{},{}\n".format(p[0], p[1], p[2], p[3], p[4], p[5], p[6])
        f = open(file, 'w')
        f.writelines(lines)


def read_kml(in_file):
    tree = etree.parse(in_file)
    root = tree.getroot()
    lines = []
    for ele in root.iter():
        if ele.tag.endswith('LineString'):
            for child in ele.iter():
                if child.tag.endswith('coordinates'):
                   line = []
                   data = child.text
                   for p in data.split(' '):
                       if p.startswith("\n\t"):
                           if p.endswith("\t"):
                               continue
                           else:
                               line.append([float(i) for i in p[p.rfind('\t') + 1:].split(',')])
                       else:
                           line.append([float(i) for i in p.split(',')])
                   lines.append(np.asarray(line))
    return lines


if __name__=="__main__":
    file_dir = os.path.realpath(__file__)
    directory = os.path.dirname(os.path.dirname(file_dir))
    data_file = os.path.join(os.path.join(directory, "test"), "testdata.kml")
    print(data_file)
    coords_set = read_kml(data_file)
    for coords in coords_set:
        gsv = GSV(coords, directory)
    print("Process finished")
