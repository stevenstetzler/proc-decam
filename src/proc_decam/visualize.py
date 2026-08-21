from pathlib import Path
from os.path import normpath
from sys import stdout, stderr

def get_images(butler, proc_type, dataset, subset='*', template_type='', coadd_subset='', where=''):
    if proc_type in ['diff_drp', 'coadd']:
        if coadd_subset == '':
            coadd_subset = '*'
        if template_type == '':
            template_type = '*'

    collection = normpath(f"{subset}/{coadd_subset}/{template_type}/{proc_type}")
    print(f"getting {dataset} from", collection, file=stderr)
    yield from butler.registry.queryDatasets(dataset, where=where, collections=butler.registry.queryCollections(collection))

def plot(exposure, filename, dpi=100):
    # return
    import lsst.afw.display as afwDisplay
    # from lsst.afw.image import ImageF, ExposureF
    import matplotlib.pyplot as plt
    afwDisplay.setDefaultBackend("matplotlib")
    width = exposure.getWidth()
    height = exposure.getHeight()
    fig_width = width / dpi
    fig_height = height / dpi

    for attr in [None, 'image', 'variance', 'mask']:
        fig = plt.figure(figsize=(fig_width, fig_height), dpi=dpi)
        ax = fig.add_axes([0, 0, 1, 1])
        display = afwDisplay.Display(frame=fig)
        if attr != 'mask':
            display.scale("asinh", "zscale")
        try:
            if attr == 'mask':
                i = exposure.mask  # bitmask - mtv handles this specially, don't scale
            elif attr and hasattr(exposure, attr):
                plane = getattr(exposure, attr)
                i = plane.convertF() if hasattr(plane, 'convertF') else plane
            else:
                mi = exposure.maskedImage
                i = mi.convertF() if hasattr(mi, 'convertF') else mi
            # print(i)
            display.mtv(i)
        except Exception as e:
            print(e, file=stderr)
            return

        ax.images[0].set_interpolation('nearest')
        ax.axis("off")
        if attr:
            f = filename / attr
        else:
            f = filename / 'exposure'
        filename.mkdir(exist_ok=True)
        f = str(f) + '.png'
        print(f"saving image to {f}", file=stderr)
        plt.savefig(
            f,
            dpi=dpi,
            bbox_inches='tight',
            pad_inches=0,
            format='png'
        )
        plt.close(fig)

def main():
    import lsst.daf.butler as dafButler
    import argparse
    from joblib import Parallel, delayed
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("proc_type", type=str)
    parser.add_argument("datasets", nargs="+", type=str)
    parser.add_argument("--subset", type=str, default='*')
    parser.add_argument("--coadd-subset", type=str, default='')
    parser.add_argument("--template-type", type=str, default='')
    parser.add_argument("--output-directory", type=Path, default="images")
    parser.add_argument("--where", type=str, default='')
    parser.add_argument("-j", "--processes", type=int, default=4)

    args = parser.parse_args()

    butler = dafButler.Butler(args.repo)
    def gen_args():
        for dataset in args.datasets:
            for image_ref in get_images(butler, args.proc_type, dataset, subset=args.subset, template_type=args.template_type, coadd_subset=args.coadd_subset, where=args.where):
                filename = Path(butler.getURI(image_ref).path)
                filename = filename.name.replace("".join(filename.suffixes), "")
                filename = filename.replace(dataset + "_", "")
                filename = args.output_directory / dataset / filename
                filename.parent.mkdir(parents=True, exist_ok=True)
                print(image_ref)
                yield image_ref, filename

    f = lambda ref, filename : plot(butler.get(ref), filename)
    Parallel(n_jobs=args.processes)(delayed(f)(*args) for args in gen_args())

if __name__ == "__main__":
    main()