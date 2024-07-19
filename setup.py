import setuptools

setuptools.setup(
    name="hypothesis-crosshair",
    version="0.0.8",
    author="Phillip Schanely",
    author_email="pschanely+B9vk@gmail.com",
    packages=setuptools.find_packages(),
    url="https://github.com/pschanely/hypothesis-crosshair",
    license="MIT",
    description="Level-up your Hypothesis tests with CrossHair.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    install_requires=["hypothesis>=6.104.2", "crosshair-tool>=0.0.58"],
    python_requires=">=3.8",
    entry_points={
        "hypothesis": ["_ = hypothesis_crosshair_provider:_hypothesis_setup_hook"]
    },
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Framework :: Hypothesis",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Testing",
    ],
)
