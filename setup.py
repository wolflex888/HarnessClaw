#!/usr/bin/env python

import os
from setuptools import setup, find_packages

here = os.path.abspath(os.path.dirname(__file__))

required = [
]

setup(
    name='harness_claw',
    version='0.0.1',
    packages=find_packages(),
    url='https://github.com/wolflex888/HarnessClaw',
    author='wolflex888',
    author_email='wolflex888@gmail.com',
    install_requires=required,
    classifiers=[
        "Programming Language :: Python :: 3.12"
    ],
)
