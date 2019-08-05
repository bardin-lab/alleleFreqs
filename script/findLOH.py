import sys, os
import fnmatch
from optparse import OptionParser
import pandas as pd
import ntpath
from collections import defaultdict
import json
import vcf


def find_normal(options):
    sample = ntpath.basename(options.freebayes_file).split("_")[0]
    config = pd.read_csv(options.config, delimiter="\t", index_col=False, na_filter=False, names=['sample', 'assay'])

    samples = config['sample'].tolist()
    it = iter(samples)

    s = {}
    for x in it:
        s[x] = next(it)

    if(s[sample]):
        print("Tumour: %s" % sample)
        print("Normal: %s" % s[sample])
        return sample, s[sample]
    else:
        print("Cant find corresponding normal sample for %s" % sample)

def parse_freebayes(options):

    tumour, normal = find_normal(options)

    vcf_reader = vcf.Reader(open(options.freebayes_file, 'r'))

    for record in vcf_reader.fetch("2L"):
        if(record.genotype(tumour)['DP'] > 20 and record.genotype(normal)['DP'] > 20 and record.genotype(tumour)['GQ'] > 1 ):
            if (record.genotype(tumour)['GT'] == '0/0' and record.genotype(normal)['GT'] == '0/0') or (record.genotype(tumour)['GT'] == '1/1' and record.genotype(normal)['GT'] == '1/1'):
                continue
            if 'snp' not in record.INFO['TYPE'] or len(record.INFO['TYPE']) > 1:
                continue

            try:
                status = record.INFO['VT']
            except KeyError:
                status = 'germline'
                pass

            # Need to skip complex calls
            # Should also skip non Germline...
            accepted_genotypes = ['0/0', '0/1', '1/0', '1/1']

            if record.genotype(tumour)['GT'] not in accepted_genotypes or record.genotype(normal)['GT'] not in accepted_genotypes:
                continue
            taf = round((record.genotype(tumour)['AO'] / (record.genotype(tumour)['AO'] + record.genotype(tumour)['RO'])),2)
            naf = round((record.genotype(normal)['AO'] / (record.genotype(normal)['AO'] + record.genotype(normal)['RO'])),2)

            af_diff = abs(naf - taf)

            t_a_count = record.genotype(tumour)['AO']
            n_a_count = record.genotype(normal)['AO']

            if(isinstance(t_a_count, list)):
                t_a_count = t_a_count[0] # Some records contain two values ... (?)
            if(isinstance(n_a_count, list)):
                n_a_count = n_a_count[0] # Some records contain two values ... (?)

            t_freq = round((t_a_count/record.genotype(tumour)['DP'])*100, 2)
            n_freq = round((n_a_count/record.genotype(normal)['DP'])*100, 2)

            difference = abs(t_freq - n_freq)

            if difference > 20:
                print("LOH", record.INFO, record.genotype(tumour))



    # print("hello")



def parse_varscan(options):

    sample = ntpath.basename(options.varscan_file).split(".")[0]
    bed_file = sample + '_LOH_regions.bed'

    df = pd.read_csv(options.varscan_file, delimiter="\t")
    df = df.sort_values(['chrom', 'position'])

    chroms = ['2L', '2R', '3L', '3R', 'X', 'Y', '4']
    loh = defaultdict(lambda: defaultdict(dict))
    start = False
    start_chain = False
    loh_count = defaultdict(int)
    last_informative_snp =  defaultdict(lambda: defaultdict(int))

    for row in df.itertuples(index=True, name='Pandas'):
        chrom, pos, n_freq, t_freq, snv_type, p_val = getattr(row, "chrom"), getattr(row, "position"), getattr(row, "normal_var_freq"), getattr(row, "tumor_var_freq"), getattr(row, "somatic_status"), float(getattr(row, "somatic_p_value"))

        if chrom not in chroms: continue

        t_freq = float(t_freq.rstrip("%"))
        n_freq = float(n_freq.rstrip("%"))
        t_freq += 0.001
        n_freq += 0.001


        if snv_type == 'LOH':
            start_chain = True
            chain = True
        elif snv_type == 'Germline' and t_freq <= 25 or t_freq >= 75:
            chain = True
            start_chain = False
        elif snv_type == 'Germline' and p_val > 0.5:
            chain = True
            start_chain = False
        elif snv_type == 'Germline' and abs(n_freq-t_freq) >= 10:
            chain = True
            start_chain = False
        elif snv_type == 'Somatic':
            chain = True
            start_chain = False
        else:
            chain = False
            start = False

        if chrom not in loh:
            start = pos

        if chain and snv_type == 'LOH' and loh_count[start] > 5:
            last_informative_snp[chrom] = pos

        if chain and start:
            loh[chrom].setdefault(start, []).append(pos)
            start_chain = False
            if snv_type == 'LOH':
                loh_count[start] += 1
        elif start_chain:
            start = pos


    loh_run = defaultdict(lambda: defaultdict(dict))

    with open(bed_file, 'w') as bed_out:
        for c in sorted(loh):
            for p in sorted(loh[c]):
                start = int(p)
                end = int(max(loh[c][p]))
                if p in loh_count:
                    if loh_count[p] >= 10 or loh_count[p] >= 5 and (end - start > 30000):
                        loh_run[c][p] = loh[c][p]
                        bed_out.write('%s\t%s\t%s\n' %(c, p, end))

    # print(json.dumps(last_informative_snp['2L'], indent=4, sort_keys=True))

    max_start = max(loh_run['2L'])
    max_end   = max(loh_run['2L'][max_start])

    print("Last LOH snp on 2L: %s" % last_informative_snp['2L'])

    breakpoint_window_start = last_informative_snp['2L']
    breakpoint_window_end   = max_end + options.window


    print("Breakpoint window on 2L (+/- %s): 2L:%s-%s" % (options.window, breakpoint_window_start, breakpoint_window_end))

    if options.write_breakpoint:
        breakpoint = sample + '_breakpoint_region.bed'
        print("Writing breakpoint region to %s" % breakpoint)
        with open(breakpoint, 'w') as breakpoint_out:
            breakpoint_out.write('%s\t%s\t%s\n' %('2L', breakpoint_window_start, breakpoint_window_end))

    # print(json.dumps(loh_run, indent=4, sort_keys=True))
    return True


def main():
    parser = OptionParser()

    parser.add_option("-v", "--varscan_file", dest="varscan_file", help="Varscan native file")
    parser.add_option("-f", "--freebayes_file", dest="freebayes_file", help="Freebayes VCF file")
    parser.add_option("-w", dest="window", action="store", help="Print window at breakpoint on 2L")
    parser.add_option("--write_breakpoint", dest="write_breakpoint", action="store_true", help="Write window at breakpoint on 2L as bed file")

    parser.add_option("--config", dest="config", action="store", help="mapping for tumour/normal samples")


    parser.set_defaults(config='/Users/Nick_curie/Desktop/script_test/alleleFreqs/data/samples.tsv',
                        window=5000)

    options, args = parser.parse_args()

    if options.varscan_file is None and options.freebayes_file is None:
        parser.print_help()
        print
    else:
        try:
            if options.freebayes_file:
                parse_freebayes(options)
            else:
                parse_varscan(options)
        except IOError as err:
            sys.stderr.write("IOError " + str(err) + "\n")
            return

if __name__ == "__main__":
    sys.exit(main())