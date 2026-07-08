from distutils.core import setup
from Cython.Build import cythonize
setup(
    name = 'calib.app',
    ext_modules = cythonize(["rosmaster_pid2.py"])
    ) 

#python3 setup.py build_ext --inplace
