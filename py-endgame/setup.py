from setuptools import setup, find_packages


setup(
    name='endgame',
    version='0.0.0',
    packages=find_packages(),
    install_requires=[
        'aiofiles',
        'aiohttp',
        'fire',
    ],
    entry_points=dict(
        console_scripts=[
            'endgame=endgame.cli:main',
        ]
    )
)
