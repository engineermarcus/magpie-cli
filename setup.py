from setuptools import setup, find_packages

setup(
    name="magpie-cli",
    version="1.0.0",
    description="Magpie CLI — High speed movie and TV show terminal downloader powered by aria2c",
    author="Magpie Team",
    packages=find_packages(),
    install_requires=[
        "httpx>=0.24.0",
        "rich>=13.0.0",
    ],
    entry_points={
        "console_scripts": [
            "magpie=magpie_cli.main:main",
        ],
    },
    python_requires=">=3.8",
)
