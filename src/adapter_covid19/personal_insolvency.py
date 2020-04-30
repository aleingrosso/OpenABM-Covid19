from dataclasses import dataclass, field
from typing import Mapping, MutableMapping, Tuple

import numpy as np
import pandas as pd
import scipy.stats

from adapter_covid19.constants import START_OF_TIME, DAYS_IN_A_YEAR
from adapter_covid19.datasources import Reader, RegionDataSource, RegionSectorAgeDataSource, RegionDecileSource
from adapter_covid19.enums import LabourState, Age
from adapter_covid19.enums import Region, Sector, Decile


@dataclass
class PersonalBankruptcyResults:
    time: int
    delta_balance: Mapping[Decile, float]
    balance: Mapping[Decile, float]
    credit_mean: Mapping[Decile, float]
    credit_std: float
    utilization: Mapping[LabourState, float]
    personal_bankruptcy: float


@dataclass
class PersonalBankruptcyModel:
    # Threshold of credit score for default
    default_th: float = np.random.rand() * 500

    # Maximum salary when furloughed
    max_earning_furloughed: float = np.random.rand() * 30000

    # Coefficient in credit score regression
    beta: float = np.random.rand() * 10

    # Ratios in simulating utilization factors
    utilization: Mapping[Region, Mapping[LabourState, float]] = field(default=None)

    # GDP per region per sector per age
    gdp_data: Mapping[Tuple[Region, Sector, Age], float] = field(default=None)

    # Credit mean by region
    credit_mean: Mapping[Region, float] = field(default=None)

    # Credit std
    credit_std: Mapping[Region, float] = field(default=None)

    # Earnings by region, decile
    earnings: Mapping[Tuple[Region, Decile], float] = field(default=None)

    # Minimum expenses by region, decile
    expenses: Mapping[Tuple[Region, Decile], float] = field(default=None)

    # Saving by region, decile
    cash_reserve: Mapping[Tuple[Region, Decile], float] = field(default=None)

    # Earning ratio per labour state
    eta: MutableMapping[LabourState, float] = field(default=None)

    # Weighting per decile
    w_decile: Mapping[Region, Mapping[Decile, float]] = field(default=None)

    # Sector weightings per region
    _sector_region_weights: Mapping[Region, Mapping[Sector, float]] = field(
        default=None, init=False
    )

    # Results, t by region by PersonalBankruptcyResults
    results: MutableMapping[
        int, MutableMapping[Region, PersonalBankruptcyResults]
    ] = field(default_factory=dict, init=False)

    def load(self,
             reader: Reader,
             corporate_bankruptcy: Mapping[Sector, float] = None
             ) -> None:
        if self.gdp_data is None:
            self.gdp_data = RegionSectorAgeDataSource("gdp").load(reader)

        df_gdp = pd.Series(self.gdp_data).to_frame().reset_index().groupby(["level_0", "level_1"])[0].sum().unstack()
        self._sector_region_weights = (df_gdp.T / df_gdp.T.sum(axis=0)).to_dict()

        if self.utilization is None:
            self.init_utilization(corporate_bankruptcy)

        if self.credit_mean is None or self.credit_std is None:
            credit_score = RegionDataSource("credit_score").load(reader)
            if self.credit_mean is None:
                self.credit_mean = credit_score["mean"]
            if self.credit_std is None:
                self.credit_std = credit_score["stdev"]

        if self.earnings is None:
            self.earnings = RegionDecileSource("earnings").load(reader)

        if self.expenses is None:
            self.expenses = RegionDecileSource("expenses").load(reader)

        if self.cash_reserve is None:
            self._init_cash_reserve()

        if self.eta is None:
            self._init_eta()

        if self.w_decile is None:
            self._init_w_decile()

        self._check_data()

    def _check_data(self) -> None:
        for source in [
            self.credit_mean,
            self.credit_std,
        ]:
            regions = set(source.keys())
            if regions != set(Region):
                raise ValueError(f"Inconsistent data: {regions}, {set(Region)}")

    def init_utilization(self,
                         corporate_bankruptcy: Mapping[Sector, float] = None,
                         utilization_ill: float = None,
                         utilization_furloughed: float = None,
                         utilization_wfh: float = None,
                         utilization_working: float = None,
                         ) -> None:
        if corporate_bankruptcy is None:
            cb_by_region = {r: np.random.rand() for r in Region}
        else:
            cb_by_region = {
                r: sum(
                    v * self._sector_region_weights[r][s]
                    for s, v in corporate_bankruptcy.items()
                )
                for r in Region
            }

        self.utilization = {}

        if utilization_ill is None:
            utilization_ill = np.random.rand()

        if utilization_furloughed is None or utilization_wfh is None or utilization_working is None:
            utilization_furloughed, utilization_wfh, utilization_working = np.random.dirichlet(([1, 1, 1]))

        for r in Region:
            utilization_r = {}
            utilization_r_sum = 0
            # We first lock lambda_unemployed
            utilization_r[LabourState.UNEMPLOYED] = cb_by_region[r]
            utilization_r_sum += utilization_r[LabourState.UNEMPLOYED]

            # Next we check lambda_ill
            utilization_r[LabourState.ILL] = min(utilization_ill, 1 - utilization_r_sum)
            utilization_r_sum += utilization_r[LabourState.ILL]

            # Next we check furloughed, wfh and working
            utilization_r[LabourState.FURLOUGHED] = utilization_furloughed * (1 - utilization_r_sum)
            utilization_r[LabourState.WFH] = utilization_wfh * (1 - utilization_r_sum)
            utilization_r[LabourState.WORKING] = utilization_working * (1 - utilization_r_sum)

            self.utilization[r] = utilization_r

    def _init_cash_reserve(self) -> None:
        self.cash_reserve = {(r, d): 0 for r in Region for d in Decile}

    def _init_eta(self) -> None:
        self.eta = {
            LabourState.ILL: 1,
            LabourState.WFH: 1,
            LabourState.WORKING: 1,
            LabourState.FURLOUGHED: 0.8,
            LabourState.UNEMPLOYED: 0,
        }

    def _init_w_decile(self) -> None:
        self.w_decile = {r: {d: 1. / len(Decile) for d in Decile} for r in Region}

    def simulate(self,
                 time: int,
                 ) -> None:
        self.results[time] = {}
        for r in Region:
            if time == START_OF_TIME:
                delta_balance = 0
                balance = {d: self.cash_reserve[(r, d)] for d in Decile}
            else:
                delta_balance = self._calc_delta_balance(r)
                balance = {d: self.results[time - 1][r].balance[d] + delta_balance[d] for d in Decile}

            spot_credit_mean = self._calc_credit_mean(self.credit_mean[r], balance)

            personal_bankruptcy = self._calc_personal_bankruptcy(r, spot_credit_mean)

            self.results[time][r] = PersonalBankruptcyResults(
                time=time,
                delta_balance=delta_balance,
                balance=balance,
                credit_mean=spot_credit_mean,
                credit_std=self.credit_std[r],
                utilization=self.utilization[r],
                personal_bankruptcy=personal_bankruptcy,
            )

    def _calc_delta_balance(self,
                            r: Region
                            ) -> Mapping[Decile, float]:
        db = {}
        for d in Decile:
            db_d = 0
            for ls in LabourState:
                spot_earnings = self.eta[ls] * self.earnings[(r, d)]
                if ls == LabourState.FURLOUGHED:
                    spot_earnings = min(spot_earnings, self.max_earning_furloughed)

                db_d += self.utilization[r][ls] * (spot_earnings - self.expenses[(r, d)]) / DAYS_IN_A_YEAR

            db[d] = db_d
        return db

    def _calc_credit_mean(self,
                          init_credit_mean: float,
                          balance: Mapping[Decile, float],
                          ) -> Mapping[Decile, float]:
        return {d: init_credit_mean + self.beta * min(balance[d], 0) for d in Decile}

    def _calc_personal_bankruptcy(self,
                                  r: Region,
                                  spot_credit_mean: Mapping[Decile, float],
                                  ) -> float:
        ppb = 0
        for d in Decile:
            ppb += self.w_decile[r][d] * scipy.stats.norm.cdf(self.default_th, spot_credit_mean[d], self.credit_std[r])

        return ppb
