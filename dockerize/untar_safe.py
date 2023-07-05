#!/usr/bin/python
# -*- coding: utf-8 -*-

import tarfile
import os
import sys

def relative_symlink(src, dst):
    target_dir = os.path.dirname(dst)
    src_rel = os.path.relpath(src, target_dir)
    dst_abs = os.path.join(target_dir, os.path.basename(dst))
    return os.symlink(src_rel, dst_abs)

def main():
    target = sys.argv[1]
    cwd = os.getcwd()

    if not os.path.isdir(target):
        os.mkdir(target)

    os.chdir(target)

    try:
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as t:
            # for m in t.getmembers():
            for m in t:
                # if "libnss_compat.so" in m.name:
                #     print(m, type(m), m.linkname, m.name, m.type)
                if m.issym() and not m.name.startswith(".") and m.linkname.startswith("/"):
                    filename = os.path.join(os.sep, m.name)
                    dirname = os.path.dirname(filename)
                    relpath = os.path.relpath(m.linkname, start=dirname)
                    src = os.path.join(target, os.path.relpath(m.linkname, start="/"))
                    dst = os.path.join(target, m.name)
                    # print("relative_symlink {} {}".format(src, dst))
                    relative_symlink(src, dst)
                else:
                    t.extract(m, target)
    except Exception as e:
        print(e)
    os.chdir(cwd)
