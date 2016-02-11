try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

setup(name='spockwebui',
      py_modules=['spockwebui'],
      version='0.1.0',
      description='A SpockBot plugin for controlling the client from the browser.',
      author='Gjum',
      author_email='code.gjum@gmail.com',
      url='https://github.com/Gjum/spockwebui',
      license='MIT',
      install_requires=[
          'websockets',  # TODO version
          # 'spockbot >= 0.2.0',  # TODO enable when released
      ],
      classifiers=[
          'Development Status :: 4 - Beta',
          'Environment :: Web Environment',
          'Intended Audience :: Developers',
          'Intended Audience :: System Administrators',
          'License :: OSI Approved :: MIT License',
          'Natural Language :: English',
          'Programming Language :: Python :: 3.3',
          'Programming Language :: Python :: 3.4',
          'Programming Language :: Python :: 3.5',
          'Topic :: Games/Entertainment',
          'Topic :: Home Automation',
          'Topic :: System :: Monitoring',
      ],
)
