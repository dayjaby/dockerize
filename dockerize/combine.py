import os
import argparse

def combine_dockerignore(dockerignore, target_dockerignore, directory):
    lines = (line.rstrip() for line in dockerignore)
    for line in lines:
        if line.startswith("/"):
            print(os.path.join(os.sep, directory, line[1:]), file=target_dockerignore)
        else:
            pass # ignore this case for now as we probably will never use it

def combine_dockerfile(dockerfile, target_dockerfile, directory):
    lines = (line.rstrip() for line in dockerfile)
    for line in lines:
        if line.startswith("FROM"):
            splits = line.split(" ")
            platform = None
            arch = None
            for split in splits:
                if split.startswith("--platform"):
                    platform = split.split("=")[1]
                    arch = platform.split("/")[-1]

            print(line + " AS " + arch, file=target_dockerfile)
        elif line == "COPY . /":
            print("COPY ./{} /".format(directory), file=target_dockerfile)
        else:
            print(line, file=target_dockerfile)
    print("", file=target_dockerfile)
    print("", file=target_dockerfile)

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument('--dir', '-d',
                       help='Directory containing extracted docker images')
    args = parser.parse_args()

    with open(os.path.join(args.dir, ".dockerignore"), "w") as dockerignore, open(os.path.join(args.dir, "Dockerfile"), "w") as dockerfile:
        for root, _, files in os.walk(args.dir):
            directory = os.path.relpath(root, args.dir)
            if root == args.dir:
                continue
            for name in files:
                path = os.path.join(root, name)
                if name == "Dockerfile":
                    with open(path, "r") as f:
                        combine_dockerfile(f, dockerfile, directory)
                elif name == ".dockerignore":
                    with open(path, "r") as f:
                        combine_dockerignore(f, dockerignore, directory)
        print("ARG TARGETARCH", file=dockerfile)
        print("FROM ${TARGETARCH}", file=dockerfile)
        print("ENV TARGETARCH=${TARGETARCH}", file=dockerfile)
