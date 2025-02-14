import re
import os
import yaml
import argparse

import torch
import numpy as np

from rdkit import Chem, RDLogger
from tqdm import tqdm

# Disable RDKit warnings
RDLogger.DisableLog("rdApp.*")


class LoadFromFile(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if values.name.endswith("yaml") or values.name.endswith("yml"):
            with values as f:
                config = yaml.load(f, Loader=yaml.FullLoader)
            for key in config.keys():
                if key not in namespace:
                    raise ValueError(f"Unknown argument in config file: {key}")
            namespace.__dict__.update(config)
        else:
            raise ValueError("Configuration file must end with yaml or yml")


def train_val_test_split(dset_len, train_size, val_size, test_size, seed):
    assert (train_size is None) + (val_size is None) + (
            test_size is None
    ) <= 1, (
        "Only one of train_size, val_size, test_size is allowed to be None."
    )

    is_float = (
        isinstance(train_size, float),
        isinstance(val_size, float),
        isinstance(test_size, float),
    )

    train_size = round(dset_len * train_size) if is_float[0] else train_size
    val_size = round(dset_len * val_size) if is_float[1] else val_size
    test_size = round(dset_len * test_size) if is_float[2] else test_size

    if train_size is None:
        train_size = dset_len - val_size - test_size
    elif val_size is None:
        val_size = dset_len - train_size - test_size
    elif test_size is None:
        test_size = dset_len - train_size - val_size

    if train_size + val_size + test_size > dset_len:
        if is_float[2]:
            test_size -= 1
        elif is_float[1]:
            val_size -= 1
        elif is_float[0]:
            train_size -= 1

    assert train_size >= 0 and val_size >= 0 and test_size >= 0, (
        f"One of training ({train_size}), validation ({val_size}) or "
        f"testing ({test_size}) splits ended up with a negative size."
    )

    total = train_size + val_size + test_size
    assert (
            dset_len >= total
    ), f"The dataset ({dset_len}) is smaller than the combined split sizes ({total})."

    if total < dset_len:
        print(f"{dset_len - total} samples were excluded from the dataset")

    idxs = np.arange(dset_len, dtype=np.int32)
    idxs = np.random.default_rng(seed).permutation(idxs)

    idx_train = idxs[:train_size]
    idx_val = idxs[train_size: train_size + val_size]
    idx_test = idxs[train_size + val_size: total]

    return np.array(idx_train), np.array(idx_val), np.array(idx_test)


def number(text):
    r"""
    Converts a string to a number.
    """
    if text is None or text == "None":
        return None

    try:
        num_int = int(text)
    except ValueError:
        num_int = None
    num_float = float(text)

    if num_int == num_float:
        return num_int

    return num_float


def make_splits(
        dataset_len: int,
        train_size: float = 0.8,
        val_size: float = 0.1,
        test_size: float = 0.1,
        seed: int = 42,
        filename: str = None,
        splits=None,
):
    r"""
    Creates train, validation and test splits for a dataset.

    Parameters
    ----------
    dataset_len : int
        The length of the dataset.
    train_size : float, optional
        The size of the training split in percentage or absolute number. The default is 0.8.
    val_size : float, optional
        The size of the validation split in percentage or absolute number. The default is 0.1.
    test_size : float, optional
        The size of the testing split in percentage or absolute number. The default is 0.1.
    seed : int, optional
        The seed for the random number generator. The default is 42.
    filename : str, optional
        The filename to save the splits to. The default is None.
    splits : str, optional
        The filename of the splits to load. The default is None.

    Returns
    -------
    tuple of torch.Tensor
        The indices of the training, validation and testing splits.
    """
    if splits is not None:
        splits = np.load(splits)
        idx_train = splits["idx_train"]
        idx_val = splits["idx_val"]
        idx_test = splits["idx_test"]
    else:
        idx_train, idx_val, idx_test = train_val_test_split(
            dataset_len, train_size, val_size, test_size, seed
        )

    if filename is not None:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        np.savez(
            filename, idx_train=idx_train, idx_val=idx_val, idx_test=idx_test
        )

    return (
        torch.from_numpy(idx_train),
        torch.from_numpy(idx_val),
        torch.from_numpy(idx_test),
    )


def save_argparse(
        args: argparse.Namespace,
        filename: str,
        exclude: list = None
):
    r"""
    Saves the argparse namespace to a file.

    Parameters
    ----------
    args : argparse.Namespace
        The argparse namespace to save.
    filename : str
        The filename to save the argparse namespace to.
    exclude : list, optional
        A list of keys to exclude from the argparse namespace. The default is None.
    """
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    if filename.endswith("yaml") or filename.endswith("yml"):
        if isinstance(exclude, str):
            exclude = [exclude]
        args = args.__dict__.copy()
        for exl in exclude:
            del args[exl]
        yaml.dump(args, open(filename, "w"))
    else:
        raise ValueError("Configuration file should end with yaml or yml")


def export_file(suppl: list, output_path: str):
    r"""
    Exports a list of RDKit molecules to a file.

    Parameters
    ----------
    suppl : list of Chem.rdchem.Mol
        The list of RDKit molecules to export.
    output_path : str
        The path to the output file.
    """
    writer = Chem.SDWriter(output_path)

    for mol in suppl:
        props = mol.GetPropsAsDict()
        writer.SetProps(list(props.keys()))

        writer.write(mol)

    writer.close()


def create_hydrogen_dataset(data_path: str):
    suppl = select_atoms(data_path)

    dataset = []

    for mol in tqdm(suppl, desc="Validating dataset", total=len(suppl)):
        # Check if the molecule is valid
        if mol is None:
            print(f"Invalid molecule found in {data_path}")
            continue

        # get the properties of the molecule
        prop_names = mol.GetPropNames(includePrivate=False, includeComputed=False)
        has_spectrum = False

        for prop in prop_names:
            pattern = r"^Spectrum 1H \d+$"
            if bool(re.match(pattern, prop)):
                has_spectrum = True
                break

        if has_spectrum:
            dataset.append(mol)

    print(f"Valid molecules found: {len(dataset)}")

    export_file(dataset, output_path='hydrogen_dataset.sdf')


def create_carbon_dataset(data_path: str):
    r"""
    Creates a dataset of carbon spectra from a given sdf file.
    
    Parameters
    ----------
    data_path : str
        The path to the sdf file containing the carbon spectra.
    """
    suppl = select_atoms(data_path)

    dataset = []

    for mol in tqdm(suppl, desc="Validating dataset", total=len(suppl)):
        # Check if the molecule is valid
        if mol is None:
            print(f"Invalid molecule found in {data_path}")
            continue

        # get the properties of the molecule
        prop_names = mol.GetPropNames(includePrivate=False, includeComputed=False)
        # check if the molecule has a carbon spectrum
        for prop in prop_names:
            pattern = r"^Spectrum 13C \d+$"
            if bool(re.match(pattern, prop)):
                dataset.append(mol)
                break
            else:
                continue

    print(f"Valid molecules found: {len(dataset)}")

    # export the dataset to a sdf file
    export_file(dataset, output_path='carbon_dataset.sdf')


def create_fluorine_dataset(data_path: str):
    r"""
    Creates a dataset of fluorine spectra from a given sdf file.
    
    Parameters
    ----------
    data_path : str
        The path to the sdf file containing the fluorin spectra.
    """
    suppl = Chem.SDMolSupplier(data_path, removeHs=False, sanitize=True)

    dataset = []

    for mol in tqdm(suppl, desc="Validating dataset", total=len(suppl)):
        # Check if the molecule is valid
        if mol is None:
            print(f"Invalid molecule found in {data_path}")
            continue

        # get the properties of the molecule
        prop_names = mol.GetPropNames(includePrivate=False, includeComputed=False)
        # check if the molecule has a carbon spectrum
        for prop in prop_names:
            pattern = r"^Spectrum 19F \d+$"
            if bool(re.match(pattern, prop)):
                dataset.append(mol)
                break
            else:
                continue

    print(f"Valid molecules found: {len(dataset)}")

    # export the dataset to a sdf file
    export_file(dataset, output_path='fluorin_dataset.sdf')


def select_atoms(data_path: str):
    r"""
    Selects target element atoms from a given sdf file.
    Only H, B, C, O, N, F, Si, P, S, Cl, Br, I

    Parameters
    ----------
    data_path : str
        The path to the sdf file containing the spectra.

    Returns
    -------
    list of Chem.rdchem.Mol
        The list of selected atoms.
    """
    # Define the set of target elements
    target_elements = {"H", "B", "C", "O", "N", "F", "Si", "P", "S", "Cl", "Br", "I"}

    suppl = Chem.SDMolSupplier(data_path, removeHs=False, sanitize=True)

    # Initialize the list to store valid molecules
    valid_molecules = []

    # Iterate over the molecules in the SDF file
    for mol in tqdm(suppl, desc="Processing molecules", total=len(suppl)):
        # Check if the molecule is valid
        if mol is None:
            print(f"Invalid molecule found in {data_path}")
            continue

        # Get the set of elements in the molecule
        elements_in_mol = {atom.GetSymbol() for atom in mol.GetAtoms()}

        # Check if the molecule contains only the target elements
        if elements_in_mol.issubset(target_elements):
            valid_molecules.append(mol)

    return valid_molecules


def create_dataset(data_path: str, element: str = 'carbon'):
    r"""
    Creates a dataset of spectra from a given sdf file.
    
    Parameters
    ----------
    data_path : str
        The path to the sdf file containing the spectra.
    element : str
        The element to create the dataset for.
        Options: 'carbon', 'hydrogen', 'fluorine'
    """
    if element == 'carbon':
        create_carbon_dataset(data_path=data_path)
    elif element == 'hydrogen':
        create_hydrogen_dataset(data_path=data_path)
    elif element == 'fluorine':
        create_fluorine_dataset(data_path=data_path)
    else:
        raise ValueError("Invalid element specified. Options: 'carbon', 'hydrogen', 'fluorine'")


if __name__ == "__main__":
    data_path = './data/nmrshiftdb2withsignals.sd'
    create_dataset(data_path=data_path, element='hydrogen')
