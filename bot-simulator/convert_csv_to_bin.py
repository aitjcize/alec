import struct
import os
import glob
import csv
import decimal

UNIT = 100000000

def main():
    bin_fn = 'data.bin'
    fmt = struct.Struct('=LLqqc')
    known = set()

    for root, dirs, files in os.walk('csv'):
        dirs.sort()
        for fn in sorted(files):
            csv_fn = os.path.join(root, fn)
            bin_fn = csv_fn.replace('csv', 'bin')
            dirname = os.path.dirname(bin_fn)

            if os.path.exists(bin_fn):
                continue
            if not os.path.exists(dirname):
                os.makedirs(dirname)

            with file(bin_fn, 'wb') as fp:
                print csv_fn
                rows = list(csv.reader(file(csv_fn)))
                if not rows:
                    continue
                rows.pop(0)
                rows.sort()

                for row in rows:
                    # example
                    # ['1466204132', '19676898', '14.425', '5.97831998', 'sell']
                    #print row
                    assert row[1] not in known
                    if row[1] in known:
                        continue
                    known.add(row[1])
                    row = [
                            int(row[0]),
                            int(row[1]),
                            int(decimal.Decimal(row[2]) * UNIT),
                            int(decimal.Decimal(row[3]) * UNIT),
                            row[4][:1] or ' ',  # maybe unknown
                            ]
                    fp.write(fmt.pack(*row))

if __name__ == '__main__':
    main()
