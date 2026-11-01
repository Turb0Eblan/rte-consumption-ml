import joblib
import pandas as pd

from rte_consumption_ml.ml.banner import LOGO


def load_model(path="models/best_model.joblib"):

    return joblib.load(path)

#================================================================================================

def load_best_name(path="models/best_models.csv"):

    log = pd.read_csv(path)

    return log["estimator"].iloc[-1]

#================================================================================================

def main():

    print_setting = False # True for debugging

    print(LOGO)
    print("=" * 100)

    model = load_model()
    name = load_best_name()

    if print_setting:
        print('model')
        print(model)
        print("=" * 100)

    print("Loaded:", name)


if __name__ == "__main__":
    main()