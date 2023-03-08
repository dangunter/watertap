#################################################################################
# WaterTAP Copyright (c) 2020-2023, The Regents of the University of California,
# through Lawrence Berkeley National Laboratory, Oak Ridge National Laboratory,
# National Renewable Energy Laboratory, and National Energy Technology
# Laboratory (subject to receipt of any required approvals from the U.S. Dept.
# of Energy). All rights reserved.
#
# Please see the files COPYRIGHT.md and LICENSE.md for full copyright and license
# information, respectively. These files are also available online at the URL
# "https://github.com/watertap-org/watertap/"
#################################################################################
import pytest
from watertap.property_models.multicomp_aq_sol_prop_pack import MCASParameterBlock
from watertap.unit_models.electrocoagulation import (
    Electrocoagulation,
    ElectrodeMaterial,
    ReactorMaterial,
    OverpotentialCalculation,
)
from watertap.costing import WaterTAPCosting
from pyomo.environ import (
    ConcreteModel,
    assert_optimal_termination,
    value,
    Param,
    Var,
    Constraint,
)
from idaes.core import (
    FlowsheetBlock,
)
from idaes.core import UnitModelCostingBlock
from idaes.core.util.model_statistics import (
    degrees_of_freedom,
    number_variables,
    number_total_constraints,
)
from pyomo.util.check_units import assert_units_consistent
from idaes.core.util.scaling import (
    calculate_scaling_factors,
    badly_scaled_var_generator,
    set_scaling_factor,
)
from idaes.core.util.testing import initialization_tester
from idaes.core.solvers import get_solver
from idaes.core.util.exceptions import ConfigurationError

__author__ = "Kurban Sitterley"

solver = get_solver()


def get_ec_comps(ec, comp=Var):
    vs = []
    for v in ec.component_objects(comp):
        if "ref" in v.name or "properties_" in v.name:
            continue
        vs.append(v.name.split("fs.ec.")[1])
    return vs


class TestEC_noTDS:
    @pytest.mark.unit
    def test_no_tds_in_feed(self):
        ec_feed_no_tds = {
            "solute_list": ["foo", "bar", "baz", "Al_3+"],
            "mw_data": {"foo": 10e-3, "bar": 222e-3, "baz": 39e-3, "Al_3+": 29.98e-3},
            "charge": {"Al_3+": 3},
        }
        error_msg = "TDS must be in feed stream for solution conductivity estimation."
        m = ConcreteModel()
        m.fs = FlowsheetBlock(dynamic=False)
        m.fs.properties = MCASParameterBlock(**ec_feed_no_tds)
        with pytest.raises(ConfigurationError, match=error_msg):
            m.fs.ec = Electrocoagulation(property_package=m.fs.properties)


class TestEC_noECion:
    @pytest.mark.unit
    def test_no_ec_ion_in_feed(self):
        error_msg = "Electrode material ion must be in feed stream with concentration set to target electrocoagulation dose."
        ec_feed_no_al = {
            "solute_list": ["cats", "eat", "fish", "TDS", "Fe_2+"],
            "mw_data": {
                "cats": 10e-3,
                "eat": 242e-3,
                "fish": 139e-3,
                "TDS": 31.4038218e-3,
                "Fe_2+": 55.845e-3,
            },
            "charge": {"Fe_2+": 2},
        }

        m = ConcreteModel()
        m.fs = FlowsheetBlock(dynamic=False)
        m.fs.properties = MCASParameterBlock(**ec_feed_no_al)
        with pytest.raises(ConfigurationError, match=error_msg):
            m.fs.ec = Electrocoagulation(property_package=m.fs.properties)

        ec_feed_no_fe = {
            "solute_list": ["cats", "eat", "fish", "TDS", "Al_3+"],
            "mw_data": {
                "cats": 10e-3,
                "eat": 242e-3,
                "fish": 139e-3,
                "TDS": 31.4038218e-3,
                "Al_3+": 29.98e-3,
            },
            "charge": {"Al_3+": 3},
        }
        m = ConcreteModel()
        m.fs = FlowsheetBlock(dynamic=False)
        m.fs.properties = MCASParameterBlock(**ec_feed_no_fe)
        with pytest.raises(ConfigurationError, match=error_msg):
            m.fs.ec = Electrocoagulation(
                property_package=m.fs.properties, electrode_material="iron"
            )


class TestElectrocoagulationAL_default:
    @pytest.fixture(scope="class")
    def ec_al(self):
        ec_feed = {
            "solute_list": ["TDS", "Al_3+"],
            "mw_data": {"TDS": 31.4038218e-3, "Al_3+": 29.98e-3},
            "charge": {"Al_3+": 3},
        }

        flow_in = 0.0438  # 1 MGD
        tds_conc = 75  # kg/m3
        ec_target_dose = 0.1  # kg/m3

        m = ConcreteModel()
        m.fs = FlowsheetBlock(dynamic=False)

        m.fs.properties = MCASParameterBlock(**ec_feed)
        m.fs.ec = ec = Electrocoagulation(property_package=m.fs.properties)
        set_scaling_factor(ec.properties_in[0].flow_mol_phase_comp["Liq", "H2O"], 1e-3)
        set_scaling_factor(ec.properties_in[0].flow_mol_phase_comp["Liq", "TDS"], 1e-3)
        set_scaling_factor(
            ec.properties_in[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1e3
        )

        set_scaling_factor(ec.properties_out[0].flow_mol_phase_comp["Liq", "H2O"], 1e-3)
        set_scaling_factor(ec.properties_out[0].flow_mol_phase_comp["Liq", "TDS"], 1e-3)
        set_scaling_factor(
            ec.properties_out[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1e3
        )

        set_scaling_factor(ec.properties_waste[0].flow_mol_phase_comp["Liq", "H2O"], 1)
        set_scaling_factor(ec.properties_waste[0].flow_mol_phase_comp["Liq", "TDS"], 1)
        set_scaling_factor(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1
        )

        calculate_scaling_factors(m)

        m.fs.ec.properties_in.calculate_state(
            var_args={
                ("flow_vol_phase", "Liq"): flow_in,
                ("conc_mass_phase_comp", ("Liq", "TDS")): tds_conc,
                ("conc_mass_phase_comp", ("Liq", "Al_3+")): ec_target_dose,
            },
            hold_state=True,
        )

        ec.properties_in[0].pressure.fix(101325)
        ec.properties_in[0].temperature.fix(298)

        ec.electrode_thick.fix(0.001)
        ec.current_density.fix(300)
        ec.electrolysis_time.fix(50)
        ec.number_electrode_pairs.fix(10)
        ec.electrode_gap.fix(0.02)
        ec.current_efficiency.fix(1.66)
        ec.overpotential.fix(1.5)

        return m

    @pytest.mark.unit
    def test_ec_al_build(self, ec_al):
        m = ec_al
        ec = m.fs.ec

        assert len(ec.config) == 7
        assert not ec.config.dynamic
        assert not ec.config.has_holdup
        assert ec.config.electrode_material is ElectrodeMaterial.aluminum
        assert ec.config.reactor_material is ReactorMaterial.pvc
        assert ec.config.overpotential_calculation is OverpotentialCalculation.fixed

        ec_al_vars = get_ec_comps(ec, comp=Var)
        assert len(ec_al_vars) == 19
        assert "overpotential_k1" not in ec_al_vars
        assert "overpotential_k2" not in ec_al_vars

        ec_al_params = get_ec_comps(ec, comp=Param)
        nernst_params = [
            "anode_cell_potential_std",
            "anode_entropy_change_std",
            "anodic_exchange_current_density",
            "cathodic_exchange_current_density",
            "cathode_cell_potential_std",
            "cathode_entropy_change_std",
            "cathode_conc_mol_hydroxide",
            "cathode_conc_mol_metal",
            "partial_pressure_H2",
            "frac_increase_temperature",
        ]
        assert len(ec_al_params) == 5
        assert not all(np in ec_al_params for np in nernst_params)

        ec_al_constr = get_ec_comps(ec, comp=Constraint)
        assert len(ec_al_constr) == 16
        assert "eq_overpotential" not in ec_al_constr

    @pytest.mark.unit
    def test_ec_al_stats(self, ec_al):
        m = ec_al
        assert_units_consistent(m)
        assert number_variables(m) == 63
        assert number_total_constraints(m) == 35
        assert degrees_of_freedom(m) == 0

    @pytest.mark.component
    def test_ec_al_init(self, ec_al):
        m = ec_al
        initialization_tester(m, unit=m.fs.ec)
        badly_scaled_var_values = {
            var.name: val for (var, val) in badly_scaled_var_generator(m)
        }
        assert not badly_scaled_var_values

    @pytest.mark.component
    def test_ec_al_solve(self, ec_al):
        m = ec_al
        results = solver.solve(m)
        assert_optimal_termination(results)
        badly_scaled_var_values = {
            var.name: val for (var, val) in badly_scaled_var_generator(m)
        }
        assert not badly_scaled_var_values

    @pytest.mark.component
    def test_ec_al_solution(self, ec_al):
        m = ec_al
        ec = m.fs.ec
        comps = m.fs.ec.config.property_package.component_list

        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "H2O"]
        ) == pytest.approx(2228.08410, rel=5e-3)
        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "TDS"]
        ) == pytest.approx(31.3815, rel=5e-3)
        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "Al_3+"]
        ) == pytest.approx(0.043829, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "H2O"]
        ) == pytest.approx(22.505899, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "TDS"]
        ) == pytest.approx(73.223571, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "Al_3+"]
        ) == pytest.approx(0.102268, rel=5e-3)
        assert value(ec.ohmic_resistance) == pytest.approx(3.1403137e-4, rel=5e-3)
        assert value(ec.charge_loading_rate) == pytest.approx(581.6246, rel=5e-3)
        assert value(ec.electrode_area_total) == pytest.approx(84.91720478, rel=5e-3)
        assert value(ec.applied_current) == pytest.approx(25475.1614, rel=5e-3)
        assert value(ec.power_required) == pytest.approx(242.014033, rel=5e-3)
        assert value(ec.cell_voltage) == pytest.approx(9.4999999, rel=5e-3)

        ## test mass balance
        for c in comps:
            assert value(
                ec.properties_in[0].flow_mass_phase_comp["Liq", c]
                - ec.properties_out[0].flow_mass_phase_comp["Liq", c]
                - ec.properties_waste[0].flow_mass_phase_comp["Liq", c]
            ) == pytest.approx(0, rel=5e-3)

    @pytest.mark.component
    def test_ec_al_costing(self, ec_al):
        m = ec_al
        ec = m.fs.ec

        m.fs.costing = WaterTAPCosting()
        ec.costing = UnitModelCostingBlock(flowsheet_costing_block=m.fs.costing)
        m.fs.costing.cost_process()
        m.fs.costing.add_LCOW(ec.properties_out[0].flow_vol_phase["Liq"])
        m.fs.costing.add_specific_energy_consumption(
            ec.properties_out[0].flow_vol_phase["Liq"]
        )

        assert degrees_of_freedom(m) == 0
        results = solver.solve(m)
        assert_optimal_termination(results)

        assert value(m.fs.costing.LCOW) == pytest.approx(1.143868, 1e-5)
        assert value(m.fs.costing.total_capital_cost) == pytest.approx(
            5756017.3947, 1e-5
        )
        assert value(m.fs.costing.specific_energy_consumption) == pytest.approx(
            1.6359774, 1e-5
        )
        assert value(ec.costing.capital_cost_reactor) == pytest.approx(
            75593.25351, 1e-5
        )
        assert value(ec.costing.capital_cost_power_supply) == pytest.approx(
            2727847.3242, 1e-5
        )
        assert value(ec.costing.capital_cost_electrodes) == pytest.approx(
            5384.142, 1e-5
        )


class TestElectrocoagulationAL_regression:  # overpotential calculation is "regression"
    @pytest.fixture(scope="class")
    def ec_al(self):
        ec_feed = {
            "solute_list": ["TDS", "Al_3+"],
            "mw_data": {"TDS": 31.4038218e-3, "Al_3+": 29.98e-3},
            "charge": {"Al_3+": 3},
        }

        flow_in = 0.0438  # 1 MGD
        tds_conc = 7.5  # kg/m3
        ec_target_dose = 0.1  # kg/m3

        m = ConcreteModel()
        m.fs = FlowsheetBlock(dynamic=False)

        m.fs.properties = MCASParameterBlock(**ec_feed)
        m.fs.ec = ec = Electrocoagulation(
            property_package=m.fs.properties, overpotential_calculation="regression"
        )

        set_scaling_factor(ec.properties_in[0].flow_mol_phase_comp["Liq", "H2O"], 1e-3)
        set_scaling_factor(ec.properties_in[0].flow_mol_phase_comp["Liq", "TDS"], 1e-3)
        set_scaling_factor(
            ec.properties_in[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1e3
        )

        set_scaling_factor(ec.properties_out[0].flow_mol_phase_comp["Liq", "H2O"], 1e-3)
        set_scaling_factor(ec.properties_out[0].flow_mol_phase_comp["Liq", "TDS"], 1e-3)
        set_scaling_factor(
            ec.properties_out[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1e3
        )

        set_scaling_factor(ec.properties_waste[0].flow_mol_phase_comp["Liq", "H2O"], 1)
        set_scaling_factor(ec.properties_waste[0].flow_mol_phase_comp["Liq", "TDS"], 1)
        set_scaling_factor(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1
        )

        calculate_scaling_factors(m)

        m.fs.ec.properties_in.calculate_state(
            var_args={
                ("flow_vol_phase", "Liq"): flow_in,
                ("conc_mass_phase_comp", ("Liq", "TDS")): tds_conc,
                ("conc_mass_phase_comp", ("Liq", "Al_3+")): ec_target_dose,
            },
            hold_state=True,
        )

        ec.properties_in[0].pressure.fix(101325)
        ec.properties_in[0].temperature.fix(298)

        # operating conditions altered from default test to fall within range for k1 and k2
        # k1 and k2 taken from Gu, et al (2009): 10.1021/ie801086c
        ec.overpotential_k1.fix(430)
        ec.overpotential_k2.fix(1000)
        ec.electrode_thick.fix(0.001)
        ec.current_density.fix(30)
        ec.electrolysis_time.fix(5)
        ec.number_electrode_pairs.fix(10)
        ec.electrode_gap.fix(0.02)
        ec.current_efficiency.fix(1.66)

        return m

    @pytest.mark.unit
    def test_ec_al_build(self, ec_al):
        m = ec_al
        ec = m.fs.ec

        assert len(ec.config) == 7
        assert not ec.config.dynamic
        assert not ec.config.has_holdup
        assert ec.config.electrode_material is ElectrodeMaterial.aluminum
        assert ec.config.reactor_material is ReactorMaterial.pvc
        assert (
            ec.config.overpotential_calculation is OverpotentialCalculation.regression
        )

        ec_al_vars = get_ec_comps(ec, comp=Var)
        assert len(ec_al_vars) == 21

        ec_al_params = get_ec_comps(ec, comp=Param)
        nernst_params = [
            "anode_cell_potential_std",
            "anode_entropy_change_std",
            "anodic_exchange_current_density",
            "cathodic_exchange_current_density",
            "cathode_cell_potential_std",
            "cathode_entropy_change_std",
            "cathode_conc_mol_hydroxide",
            "cathode_conc_mol_metal",
            "partial_pressure_H2",
            "frac_increase_temperature",
        ]
        assert len(ec_al_params) == 5
        assert not all(np in ec_al_params for np in nernst_params)

        ec_al_constr = get_ec_comps(ec, comp=Constraint)
        assert len(ec_al_constr) == 17

    @pytest.mark.unit
    def test_ec_al_stats(self, ec_al):
        m = ec_al
        assert_units_consistent(m)
        assert number_variables(m) == 65
        assert number_total_constraints(m) == 36
        assert degrees_of_freedom(m) == 0

    @pytest.mark.component
    def test_ec_al_init(self, ec_al):
        m = ec_al
        initialization_tester(m, unit=m.fs.ec)
        badly_scaled_var_values = {
            var.name: val for (var, val) in badly_scaled_var_generator(m)
        }
        assert not badly_scaled_var_values

    @pytest.mark.component
    def test_ec_al_solve(self, ec_al):
        m = ec_al
        results = solver.solve(m)
        assert_optimal_termination(results)
        badly_scaled_var_values = {
            var.name: val for (var, val) in badly_scaled_var_generator(m)
        }
        assert not badly_scaled_var_values

    @pytest.mark.component
    def test_ec_al_solution(self, ec_al):
        m = ec_al
        ec = m.fs.ec
        comps = m.fs.ec.config.property_package.component_list

        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "H2O"]
        ) == pytest.approx(2390.6916, rel=5e-3)
        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "TDS"]
        ) == pytest.approx(3.13815307, rel=5e-3)
        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "Al_3+"]
        ) == pytest.approx(0.043829, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "H2O"]
        ) == pytest.approx(24.1483999999, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "TDS"]
        ) == pytest.approx(7.322357, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "Al_3+"]
        ) == pytest.approx(0.102268, rel=5e-3)
        assert value(ec.overpotential) == pytest.approx(1.4724032, rel=5e-3)
        assert value(ec.ohmic_resistance) == pytest.approx(0.00031403, rel=5e-3)
        assert value(ec.charge_loading_rate) == pytest.approx(581.6246, rel=5e-3)
        assert value(ec.electrode_area_total) == pytest.approx(849.17204, rel=5e-3)
        assert value(ec.applied_current) == pytest.approx(25475.1614, rel=5e-3)
        assert value(ec.power_required) == pytest.approx(241.3110, rel=5e-3)
        assert value(ec.cell_voltage) == pytest.approx(9.47240, rel=5e-3)

        ## test mass balance
        for c in comps:
            assert value(
                ec.properties_in[0].flow_mass_phase_comp["Liq", c]
                - ec.properties_out[0].flow_mass_phase_comp["Liq", c]
                - ec.properties_waste[0].flow_mass_phase_comp["Liq", c]
            ) == pytest.approx(0, rel=5e-3)

    @pytest.mark.component
    def test_ec_al_costing(self, ec_al):
        m = ec_al
        ec = m.fs.ec

        m.fs.costing = WaterTAPCosting()
        ec.costing = UnitModelCostingBlock(flowsheet_costing_block=m.fs.costing)
        m.fs.costing.cost_process()
        m.fs.costing.add_LCOW(ec.properties_out[0].flow_vol_phase["Liq"])
        m.fs.costing.add_specific_energy_consumption(
            ec.properties_out[0].flow_vol_phase["Liq"]
        )

        assert degrees_of_freedom(m) == 0
        results = solver.solve(m)
        assert_optimal_termination(results)

        assert value(m.fs.costing.LCOW) == pytest.approx(1.0893840, 1e-5)
        assert value(m.fs.costing.total_capital_cost) == pytest.approx(
            5755388.45189, 1e-5
        )
        assert value(m.fs.costing.specific_energy_consumption) == pytest.approx(
            1.55407465, 1e-5
        )
        assert value(ec.costing.capital_cost_reactor) == pytest.approx(
            26821.49848, 1e-5
        )
        assert value(ec.costing.capital_cost_power_supply) == pytest.approx(
            2727847.3242, 1e-5
        )
        assert value(ec.costing.capital_cost_electrodes) == pytest.approx(
            53841.42622, 1e-5
        )


class TestElectrocoagulationAL_nernst:  # overpotential calculation is "nernst"
    @pytest.fixture(scope="class")
    def ec_al(self):
        ec_feed = {
            "solute_list": ["TDS", "Al_3+"],
            "mw_data": {"TDS": 31.4038218e-3, "Al_3+": 29.98e-3},
            "charge": {"Al_3+": 3},
        }

        flow_in = 0.0438  # 1 MGD
        tds_conc = 75  # kg/m3
        ec_target_dose = 0.1  # kg/m3

        m = ConcreteModel()
        m.fs = FlowsheetBlock(dynamic=False)

        m.fs.properties = MCASParameterBlock(**ec_feed)
        m.fs.ec = ec = Electrocoagulation(
            property_package=m.fs.properties, overpotential_calculation="nernst"
        )

        set_scaling_factor(ec.properties_in[0].flow_mol_phase_comp["Liq", "H2O"], 1e-3)
        set_scaling_factor(ec.properties_in[0].flow_mol_phase_comp["Liq", "TDS"], 1e-3)
        set_scaling_factor(
            ec.properties_in[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1e3
        )

        set_scaling_factor(ec.properties_out[0].flow_mol_phase_comp["Liq", "H2O"], 1e-3)
        set_scaling_factor(ec.properties_out[0].flow_mol_phase_comp["Liq", "TDS"], 1e-3)
        set_scaling_factor(
            ec.properties_out[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1e3
        )

        set_scaling_factor(ec.properties_waste[0].flow_mol_phase_comp["Liq", "H2O"], 1)
        set_scaling_factor(ec.properties_waste[0].flow_mol_phase_comp["Liq", "TDS"], 1)
        set_scaling_factor(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1
        )

        calculate_scaling_factors(m)

        m.fs.ec.properties_in.calculate_state(
            var_args={
                ("flow_vol_phase", "Liq"): flow_in,
                ("conc_mass_phase_comp", ("Liq", "TDS")): tds_conc,
                ("conc_mass_phase_comp", ("Liq", "Al_3+")): ec_target_dose,
            },
            hold_state=True,
        )

        ec.properties_in[0].pressure.fix(101325)
        ec.properties_in[0].temperature.fix(298)

        ec.electrode_thick.fix(0.001)
        ec.current_density.fix(300)
        ec.electrolysis_time.fix(50)
        ec.number_electrode_pairs.fix(10)
        ec.electrode_gap.fix(0.02)
        ec.current_efficiency.fix(1.66)

        return m

    @pytest.mark.unit
    def test_ec_al_build(self, ec_al):
        m = ec_al
        ec = m.fs.ec

        assert len(ec.config) == 7
        assert not ec.config.dynamic
        assert not ec.config.has_holdup
        assert ec.config.electrode_material is ElectrodeMaterial.aluminum
        assert ec.config.reactor_material is ReactorMaterial.pvc
        assert ec.config.overpotential_calculation is OverpotentialCalculation.nernst

        ec_al_vars = get_ec_comps(ec, comp=Var)
        assert len(ec_al_vars) == 19

        ec_al_params = get_ec_comps(ec, comp=Param)
        nernst_params = [
            "anode_cell_potential_std",
            "anode_entropy_change_std",
            "anodic_exchange_current_density",
            "cathodic_exchange_current_density",
            "cathode_cell_potential_std",
            "cathode_entropy_change_std",
            "cathode_conc_mol_hydroxide",
            "cathode_conc_mol_metal",
            "partial_pressure_H2",
            "frac_increase_temperature",
        ]
        assert len(ec_al_params) == 5 + len(nernst_params)
        assert all(np in ec_al_params for np in nernst_params)

        ec_al_constr = get_ec_comps(ec, comp=Constraint)
        assert len(ec_al_constr) == 18

    @pytest.mark.unit
    def test_ec_al_stats(self, ec_al):
        m = ec_al
        # assert_units_consistent(m)
        assert number_variables(m) == 63
        assert number_total_constraints(m) == 37
        assert degrees_of_freedom(m) == 0

    @pytest.mark.component
    def test_ec_al_init(self, ec_al):
        m = ec_al
        initialization_tester(m, unit=m.fs.ec)
        badly_scaled_var_values = {
            var.name: val for (var, val) in badly_scaled_var_generator(m)
        }
        assert not badly_scaled_var_values

    @pytest.mark.component
    def test_ec_al_solve(self, ec_al):
        m = ec_al
        results = solver.solve(m)
        assert_optimal_termination(results)
        badly_scaled_var_values = {
            var.name: val for (var, val) in badly_scaled_var_generator(m)
        }
        assert not badly_scaled_var_values

    @pytest.mark.component
    def test_ec_al_solution(self, ec_al):
        m = ec_al
        ec = m.fs.ec
        comps = m.fs.ec.config.property_package.component_list

        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "H2O"]
        ) == pytest.approx(2228.084, rel=5e-3)
        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "TDS"]
        ) == pytest.approx(31.38153076, rel=5e-3)
        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "Al_3+"]
        ) == pytest.approx(0.043829219, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "H2O"]
        ) == pytest.approx(22.5058999, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "TDS"]
        ) == pytest.approx(73.223571, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "Al_3+"]
        ) == pytest.approx(0.10226817, rel=5e-3)
        assert value(ec.overpotential) == pytest.approx(1.4547750566, rel=5e-3)
        assert value(ec.ohmic_resistance) == pytest.approx(0.00031403137, rel=5e-3)
        assert value(ec.charge_loading_rate) == pytest.approx(581.6246, rel=5e-3)
        assert value(ec.electrode_area_total) == pytest.approx(84.91720, rel=5e-3)
        assert value(ec.applied_current) == pytest.approx(25475.1614, rel=5e-3)
        assert value(ec.power_required) == pytest.approx(240.8619208, rel=5e-3)
        assert value(ec.cell_voltage) == pytest.approx(9.454775, rel=5e-3)

        ## test mass balance
        for c in comps:
            assert value(
                ec.properties_in[0].flow_mass_phase_comp["Liq", c]
                - ec.properties_out[0].flow_mass_phase_comp["Liq", c]
                - ec.properties_waste[0].flow_mass_phase_comp["Liq", c]
            ) == pytest.approx(0, rel=5e-3)

    @pytest.mark.component
    def test_ec_al_costing(self, ec_al):
        m = ec_al
        ec = m.fs.ec

        m.fs.costing = WaterTAPCosting()
        ec.costing = UnitModelCostingBlock(flowsheet_costing_block=m.fs.costing)
        m.fs.costing.cost_process()
        m.fs.costing.add_LCOW(ec.properties_out[0].flow_vol_phase["Liq"])
        m.fs.costing.add_specific_energy_consumption(
            ec.properties_out[0].flow_vol_phase["Liq"]
        )

        assert degrees_of_freedom(m) == 0
        results = solver.solve(m)
        assert_optimal_termination(results)

        assert value(m.fs.costing.LCOW) == pytest.approx(1.143322, 1e-5)
        assert value(m.fs.costing.total_capital_cost) == pytest.approx(
            5756017.39477, 1e-5
        )
        assert value(m.fs.costing.specific_energy_consumption) == pytest.approx(
            1.62818931, 1e-5
        )
        assert value(ec.costing.capital_cost_reactor) == pytest.approx(
            75593.25351, 1e-5
        )
        assert value(ec.costing.capital_cost_power_supply) == pytest.approx(
            2727847.324, 1e-5
        )
        assert value(ec.costing.capital_cost_electrodes) == pytest.approx(
            5384.14262, 1e-5
        )


class TestElectrocoagulationFE_ss:  # overpotential calculation is "regression", reactor material is stainless steel
    @pytest.fixture(scope="class")
    def ec_fe(self):
        ec_feed = {
            "solute_list": ["TDS", "Fe_2+"],
            "mw_data": {"TDS": 31.4038218e-3, "Fe_2+": 55.845e-3},
            "charge": {"Fe_2+": 2},
        }

        flow_in = 0.0438  # 1 MGD
        tds_conc = 7.5  # kg/m3
        ec_target_dose = 0.1  # kg/m3

        m = ConcreteModel()
        m.fs = FlowsheetBlock(dynamic=False)

        m.fs.properties = MCASParameterBlock(**ec_feed)
        m.fs.ec = ec = Electrocoagulation(
            property_package=m.fs.properties,
            electrode_material="iron",
            reactor_material="stainless_steel",
            overpotential_calculation="regression",
        )

        set_scaling_factor(ec.properties_in[0].flow_mol_phase_comp["Liq", "H2O"], 1e-3)
        set_scaling_factor(ec.properties_in[0].flow_mol_phase_comp["Liq", "TDS"], 1e-3)
        set_scaling_factor(
            ec.properties_in[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1e3
        )

        set_scaling_factor(ec.properties_out[0].flow_mol_phase_comp["Liq", "H2O"], 1e-3)
        set_scaling_factor(ec.properties_out[0].flow_mol_phase_comp["Liq", "TDS"], 1e-3)
        set_scaling_factor(
            ec.properties_out[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1e3
        )

        set_scaling_factor(ec.properties_waste[0].flow_mol_phase_comp["Liq", "H2O"], 1)
        set_scaling_factor(ec.properties_waste[0].flow_mol_phase_comp["Liq", "TDS"], 1)
        set_scaling_factor(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", ec.ec_ion], 1
        )

        calculate_scaling_factors(m)

        m.fs.ec.properties_in.calculate_state(
            var_args={
                ("flow_vol_phase", "Liq"): flow_in,
                ("conc_mass_phase_comp", ("Liq", "TDS")): tds_conc,
                ("conc_mass_phase_comp", ("Liq", "Fe_2+")): ec_target_dose,
            },
            hold_state=True,
        )

        ec.properties_in[0].pressure.fix(101325)
        ec.properties_in[0].temperature.fix(298)

        ec.electrode_thick.fix(0.001)
        ec.current_density.fix(30)
        ec.electrolysis_time.fix(5)
        ec.number_electrode_pairs.fix(10)
        ec.electrode_gap.fix(0.02)
        ec.current_efficiency.fix(1)

        ec.overpotential_k1.fix(75)
        ec.overpotential_k2.fix(600)

        return m

    @pytest.mark.unit
    def test_ec_fe_build(self, ec_fe):
        m = ec_fe
        ec = m.fs.ec

        assert len(ec.config) == 7
        assert not ec.config.dynamic
        assert not ec.config.has_holdup
        assert ec.config.electrode_material is ElectrodeMaterial.iron
        assert ec.config.reactor_material is ReactorMaterial.stainless_steel
        assert (
            ec.config.overpotential_calculation is OverpotentialCalculation.regression
        )

        ec_fe_vars = get_ec_comps(ec, comp=Var)
        assert len(ec_fe_vars) == 21

        ec_fe_params = get_ec_comps(ec, comp=Param)
        nernst_params = [
            "anode_cell_potential_std",
            "anode_entropy_change_std",
            "anodic_exchange_current_density",
            "cathodic_exchange_current_density",
            "cathode_cell_potential_std",
            "cathode_entropy_change_std",
            "cathode_conc_mol_hydroxide",
            "cathode_conc_mol_metal",
            "partial_pressure_H2",
            "frac_increase_temperature",
        ]
        assert len(ec_fe_params) == 5
        assert not all(np in ec_fe_params for np in nernst_params)

        ec_fe_constr = get_ec_comps(ec, comp=Constraint)
        assert len(ec_fe_constr) == 17

    @pytest.mark.unit
    def test_ec_fe_stats(self, ec_fe):
        m = ec_fe
        assert_units_consistent(m)
        assert number_variables(m) == 65
        assert number_total_constraints(m) == 36
        assert degrees_of_freedom(m) == 0

    @pytest.mark.component
    def test_ec_fe_init(self, ec_fe):
        m = ec_fe
        initialization_tester(m, unit=m.fs.ec)
        badly_scaled_var_values = {
            var.name: val for (var, val) in badly_scaled_var_generator(m)
        }
        assert not badly_scaled_var_values

    @pytest.mark.component
    def test_ec_fe_solve(self, ec_fe):
        m = ec_fe
        results = solver.solve(m)
        assert_optimal_termination(results)
        badly_scaled_var_values = {
            var.name: val for (var, val) in badly_scaled_var_generator(m)
        }
        assert not badly_scaled_var_values

    @pytest.mark.component
    def test_ec_fe_solution(self, ec_fe):
        m = ec_fe
        ec = m.fs.ec
        comps = m.fs.ec.config.property_package.component_list

        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "H2O"]
        ) == pytest.approx(2390.6915, rel=5e-3)
        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "TDS"]
        ) == pytest.approx(3.138153, rel=5e-3)
        assert value(
            ec.properties_out[0].flow_mol_phase_comp["Liq", "Fe_2+"]
        ) == pytest.approx(0.023529, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "H2O"]
        ) == pytest.approx(24.148399, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "TDS"]
        ) == pytest.approx(7.32235717, rel=5e-3)
        assert value(
            ec.properties_waste[0].flow_mol_phase_comp["Liq", "Fe_2+"]
        ) == pytest.approx(0.0549019, rel=5e-3)
        assert value(ec.overpotential) == pytest.approx(0.6823959, rel=5e-3)
        assert value(ec.ohmic_resistance) == pytest.approx(0.000528577, rel=5e-3)
        assert value(ec.charge_loading_rate) == pytest.approx(345.546896, rel=5e-3)
        assert value(ec.electrode_area_total) == pytest.approx(504.49846, rel=5e-3)
        assert value(ec.applied_current) == pytest.approx(15134.9540, rel=5e-3)
        assert value(ec.power_required) == pytest.approx(131.4076633, rel=5e-3)
        assert value(ec.cell_voltage) == pytest.approx(8.6823959, rel=5e-3)

        ## test mass balance
        for c in comps:
            assert value(
                ec.properties_in[0].flow_mass_phase_comp["Liq", c]
                - ec.properties_out[0].flow_mass_phase_comp["Liq", c]
                - ec.properties_waste[0].flow_mass_phase_comp["Liq", c]
            ) == pytest.approx(0, rel=5e-3)

    @pytest.mark.component
    def test_ec_fe_costing(self, ec_fe):
        m = ec_fe
        ec = m.fs.ec

        m.fs.costing = WaterTAPCosting()
        ec.costing = UnitModelCostingBlock(flowsheet_costing_block=m.fs.costing)
        m.fs.costing.cost_process()
        m.fs.costing.add_LCOW(ec.properties_out[0].flow_vol_phase["Liq"])
        m.fs.costing.add_specific_energy_consumption(
            ec.properties_out[0].flow_vol_phase["Liq"]
        )

        assert degrees_of_freedom(m) == 0
        results = solver.solve(m)
        assert_optimal_termination(results)

        assert value(m.fs.costing.LCOW) == pytest.approx(1.23351, 1e-5)
        assert value(m.fs.costing.total_capital_cost) == pytest.approx(
            6451320.87392, 1e-5
        )
        assert value(m.fs.costing.specific_energy_consumption) == pytest.approx(
            0.846282, 1e-5
        )
        assert value(ec.costing.capital_cost_reactor) == pytest.approx(
            1229519.06995, 1e-5
        )
        assert value(ec.costing.capital_cost_power_supply) == pytest.approx(
            1831696.0183, 1e-5
        )
        assert value(ec.costing.capital_cost_electrodes) == pytest.approx(
            95261.37168, 1e-5
        )
