#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
This code demonstrates how to use RecordLink with two comma separated
values (CSV) files. We have listings of products from two different
online stores. The task is to link products between the datasets.

The output will be a CSV with our linkded results.

"""
import os
import csv
import re
import logging
import sys
import pathlib

import dedupe


def preProcess(column):
    """
    Do a little bit of data cleaning with the help of Unidecode and Regex.
    Things like casing, extra spaces, quotes and new lines can be ignored.
    """

    column = re.sub("\n", " ", column)
    column = re.sub(r"\bn/a\b", "", column)
    column = re.sub(r"\bna\b", "", column)
    column = re.sub("/", " ", column)
    column = re.sub("-", " ", column)
    column = re.sub("'", "", column)
    column = re.sub(",", "", column)
    column = re.sub(":", " ", column)
    column = re.sub(r"\blocal\b", "", column)
    column = re.sub("  +", " ", column)
    column = column.strip().strip('"').strip("'").lower().strip()
    column = " ".join(token.lstrip("0") for token in column.split())

    if not column:
        column = None
    return column


def readData(filename):
    """
    Read in our data from a CSV file and create a dictionary of records,
    where the key is a unique record ID.
    """

    data_d = {}

    with open(filename) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            clean_row = dict([(k, preProcess(v)) for (k, v) in row.items()])
            data_d[str(filename) + str(i)] = dict(clean_row)

    return data_d


def readMessyData(filename):

    data_d = {}

    with open(filename) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            clean_row = dict([(k, preProcess(v)) for (k, v) in row.items()])
            clean_row["abbr_local_name"] = clean_row["full_local_name"] = clean_row[
                "union_name"
            ]
            clean_row["city"] = clean_row["union_city"]
            clean_row["state"] = clean_row["union_state"]
            data_d[str(filename) + str(i)] = dict(clean_row)

    return data_d


def main():

    import argparse
    import sys

    # ## Logging

    # dedupe uses Python logging to show or suppress verbose output. Added for convenience.
    # To enable verbose logging, run `python examples/csv_example/csv_example.py -v`
    optp = argparse.ArgumentParser()
    optp.add_argument("infile", nargs=1)
    optp.add_argument(
        "outfile", nargs="?", type=argparse.FileType("w"), default=sys.stdout
    )
    optp.add_argument(
        "-t",
        dest="training_filename",
        nargs="?",
    )
    optp.add_argument("--verbose", "-v", action="count", default=0)
    # add verbose back in a second
    args = optp.parse_args()
    log_level = logging.WARNING
    if args.verbose:
        if args.verbose == 1:
            log_level = logging.INFO
        elif args.verbose >= 2:
            log_level = logging.DEBUG
    logging.basicConfig()
    logging.getLogger().setLevel(log_level)

    # ## Setup

    output_file = args.outfile
    settings_file = pathlib.Path(__file__).parent / "link_settings.pickle"
    training_file = args.training_filename

    (left_file,) = args.infile
    right_file = pathlib.Path(__file__).parent / "data" / "opdr_local.csv"

    print("importing data ...")
    data_1 = readMessyData(left_file)
    data_2 = readData(right_file)

    def abbr_names():
        for dataset in (data_1, data_2):
            for record in dataset.values():
                yield record["abbr_local_name"]

    def full_names():
        for dataset in (data_1, data_2):
            for record in dataset.values():
                yield record["full_local_name"]

    # ## Training

    if not training_file and os.path.exists(settings_file):
        print("reading from", settings_file)
        with open(settings_file, "rb") as sf:
            linker = dedupe.StaticRecordLink(sf)

    else:
        # Define the fields the linker will pay attention to
        #
        # Notice how we are telling the linker to use a custom field comparator
        # for the 'price' field.
        fields = [
            {"field": "abbr_local_name", "type": "ShortString"},
            {"field": "full_local_name", "type": "ShortString"},
            {"field": "abbr_local_name", "type": "Text", "corpus": abbr_names()},
            {"field": "full_local_name", "type": "Text", "corpus": full_names()},
            {"field": "city", "type": "String", "has missing": True},
            {"field": "state", "type": "String", "has missing": True},
        ]

        # add interaction for BAC

        # Create a new linker object and pass our data model to it.
        linker = dedupe.RecordLink(fields)

        # If we have training data saved from a previous run of linker,
        # look for it an load it in.
        # __Note:__ if you want to train from scratch, delete the training_file
        if os.path.exists(training_file):
            print("reading labeled examples from ", training_file)
            with open(training_file) as tf:
                linker.prepare_training(
                    data_1, data_2, training_file=tf, sample_size=15000
                )
        else:
            linker.prepare_training(data_1, data_2, sample_size=15000)

        # ## Active learning
        # Dedupe will find the next pair of records
        # it is least certain about and ask you to label them as matches
        # or not.
        # use 'y', 'n' and 'u' keys to flag duplicates
        # press 'f' when you are finished
        print("starting active labeling...")

        dedupe.console_label(linker)

        linker.train()

        # When finished, save our training away to disk
        with open(training_file, "w") as tf:
            linker.write_training(tf)

        # Save our weights and predicates to disk.  If the settings file
        # exists, we will skip all the training and learning next time we run
        # this file.
        with open(settings_file, "wb") as sf:
            linker.write_settings(sf)

        exit()

    # ## Blocking

    # ## Clustering

    # Find the threshold that will maximize a weighted average of our
    # precision and recall.  When we set the recall weight to 2, we are
    # saying we care twice as much about recall as we do precision.
    #
    # If we had more data, we would not pass in all the blocked data into
    # this function but a representative sample.

    print("clustering...")
    linked_records = linker.join(data_1, data_2, 0.0, constraint="many-to-one")

    print("# duplicate sets", len(linked_records))
    # ## Writing Results

    # Write our original data back out to a CSV with a new column called
    # 'Cluster ID' which indicates which records refer to each other.

    match = {}
    for cluster_id, (cluster, score) in enumerate(linked_records):
        source, canonical = cluster
        match[source] = {"canon_id": data_2[canonical]["f_num"], "Link Score": score}

    with open(left_file) as f_input:
        reader = csv.DictReader(f_input)

        fieldnames = ["canon_id", "Link Score"] + reader.fieldnames

        writer = csv.DictWriter(output_file, fieldnames=fieldnames)

        writer.writeheader()

        for row_id, row in enumerate(reader):

            record_id = str(left_file) + str(row_id)
            cluster_details = match.get(record_id, {})
            row.update(cluster_details)

            writer.writerow(row)
