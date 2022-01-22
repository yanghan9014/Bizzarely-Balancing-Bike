import sys
import time
import rclpy
from rclpy.node import Node
from rclpy import qos
from sensor_msgs.msg import Image as msg_Image
from sensor_msgs.msg import Imu as msg_Imu
import numpy as np
import inspect
import ctypes
import struct
import os
import threading
import matplotlib
import matplotlib.pyplot as plt
import cv2
from rclpy.executors import MultiThreadedExecutor

from rs_ros.stm_action import MainActionClient
from rs_ros.find_color_center import color_center_dist

if (os.getenv('ROS_DISTRO') != "dashing"):
    import tf2_ros

class CWaitForMessage(Node):
    def __init__(self, lock, themes, params={}):
        super().__init__('rs_listener')
        self.result = None
        self.lock = lock

        self.break_timeout = False
        self.timeout = params.get('timeout_secs', -1)
        self.time = params.get('time', None)
        self.node_name = params.get('node_name', 'rs2_listener')
        self.listener = None
        self.prev_msg_time = 0
        self.fout = None
        print ('connect to ROS with name: %s' % self.node_name)

        self.themes = {'depthStream': {'topic': '/camera/depth/image_rect_raw', 'callback': self.imageColorCallback, 'msg_type': msg_Image},
                       'colorStream': {'topic': '/camera/color/image_raw', 'callback': self.imageColorCallback, 'msg_type': msg_Image},
                       'alignedDepthColor': {'topic': '/camera/aligned_depth_to_color/image_raw', 'callback': self.imageColorCallback, 'msg_type': msg_Image},
                       }

        self.func_data = dict()

        self.declare_parameter('c1', 'R')
        self.declare_parameter('c2', 'Y')
        self.declare_parameter('c3', 'G')
        # self.wait_for_messages(themes)

    def image_msg_to_numpy(self, data):
        fmtString = data.encoding
        if fmtString in ['mono8', '8UC1', 'bgr8', 'rgb8', 'bgra8', 'rgba8']:
            img = np.frombuffer(data.data, np.uint8)
        elif fmtString in ['mono16', '16UC1', '16SC1']:
            img = np.frombuffer(data.data, np.uint16)
        elif fmtString == '32FC1':
            img = np.frombuffer(data.data, np.float32)
        else:
            print('image format not supported:' + fmtString)
            return None
    
        depth = data.step / (data.width * img.dtype.itemsize)
        if depth > 1:
            img = img.reshape(data.height, data.width, int(depth))
        else:
            img = img.reshape(data.height, data.width)
        return img

    def imageColorCallback(self, theme_name):
        def _imageColorCallback(data):
            self.prev_time = time.time()
            self.func_data[theme_name].setdefault('avg', [])
            self.func_data[theme_name].setdefault('ok_percent', [])
            self.func_data[theme_name].setdefault('num_channels', [])
            self.func_data[theme_name].setdefault('shape', [])
            self.func_data[theme_name].setdefault('reported_size', [])
            self.func_data[theme_name].setdefault('data', [])
            # self.func_data[theme_name].setdefault('color_dep', [])
            # self.func_data[theme_name].setdefault('est_dist', [])

            pyimg = self.image_msg_to_numpy(data)

            self.lock.acquire()

            if theme_name == 'alignedDepthColor':
                # np.set_printoptions(threshold=sys.maxsize)
                # print(pyimg)
                pyimg = np.where(pyimg < 1500, pyimg, 1500)
                pyimg = np.where(pyimg != 0, pyimg, 1500)
                if pyimg.size != 307200:
                    self.depth_failed = True
                else:
                    self.depth_failed = False
               # matplotlib.image.imsave( 'depth' + '.png', pyimg, cmap='gray')
            # if theme_name == 'colorStream':
                # dep = np.zeros(1)
                # try: 
                #     dep = self.func_data['alignedDepthColor']['data']
                #     dep3 = np.expand_dims(dep, axis=2)
                #     dep3 = np.concatenate((dep3, dep3, dep3), axis=2)
                #     pyimg = np.where(dep3 != 1500, pyimg, 255 )
                # except (KeyError, ValueError) as e:
                #     # print("[Runtime Error] Color depth-masking error:", e)
                #     pass

                # cen = color_center_dist(pyimg, dep, 'R')
                # if cen != None:
                    # image_dot = cv2.circle(pyimg, (cen[1], cen[0]), radius=5, color=(0, 0, 255), thickness=-1)
                    # print("Color dep:", cen[3], ",  Estimated horizontal distance:", cen[4], "mm" )

                    # self.lock.acquire()
                    # self.func_data[theme_name]['color_dep'] = cen[3]
                    # self.func_data[theme_name]['est_dist'] = cen[4]
                    # self.lock.release()

                    # matplotlib.image.imsave( 'mask' + '.png', cen[2], cmap='gray' )
                # matplotlib.image.imsave( 'color' + '.png', pyimg )

            channels = pyimg.shape[2] if len(pyimg.shape) > 2 else 1
            ok_number = (pyimg != 0).sum()

            self.func_data[theme_name]['avg'] = pyimg.sum() / ok_number
            self.func_data[theme_name]['ok_percent'] = float(ok_number) / (pyimg.shape[0] * pyimg.shape[1]) / channels
            self.func_data[theme_name]['num_channels'] = channels
            self.func_data[theme_name]['shape'] = pyimg.shape
            self.func_data[theme_name]['reported_size'] = (data.width, data.height, data.step)
            self.func_data[theme_name]['data'] = pyimg

            self.lock.release()

            print(".", end='')
            # print(self.func_data)

        return _imageColorCallback

    @staticmethod
    def unregister_all(node, registers):
        for test_name in registers:
            node.get_logger().info('Un-Subscribing test %s' % test_name)
            node.destroy_subscription(registers[test_name]['sub'])
            registers[test_name]['sub'] = None  # unregisters.

    def wait_for_messages(self, themes, exe):
        # tests_params = {<name>: {'callback', 'topic', 'msg_type', 'internal_params'}}
        self.func_data = dict([[theme_name, {}] for theme_name in themes])

        # rclpy.init()
        # node = Node('wait_for_messages')
        for theme_name in themes:
            theme = self.themes[theme_name]
            self.get_logger().info('Subscribing %s on topic: %s' % (theme_name, theme['topic']))
            self.func_data[theme_name]['sub'] = self.create_subscription(theme['msg_type'], theme['topic'], theme['callback'](theme_name), qos.qos_profile_sensor_data)

        if (os.getenv('ROS_DISTRO') != "dashing"):
            self.tfBuffer = tf2_ros.Buffer()
            self.tf_listener = tf2_ros.TransformListener(self.tfBuffer, self)

        self.prev_time = time.time()
        break_timeout = False
        while not break_timeout:
            try:
                exe.spin_once()
                # rclpy.spin_once(self, timeout_sec=1)
            except Exception as e:
                print("rs listener executor error: ", e)
                pass
            if self.timeout > 0 and time.time() - self.prev_time > self.timeout:
                break_timeout = True
                self.unregister_all(node, self.func_data)

        # node.destroy_node()
        return self.func_data


