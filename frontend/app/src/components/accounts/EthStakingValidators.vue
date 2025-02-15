<script setup lang="ts">
import { Blockchain } from '@rotki/common';
import { TaskType } from '@/types/task-type';
import { Section } from '@/types/status';
import PercentageDisplay from '@/components/display/PercentageDisplay.vue';
import HashLink from '@/components/helper/HashLink.vue';
import { SavedFilterLocation } from '@/types/filtering';
import type { Collection } from '@/types/collection';
import type { Filters, Matcher } from '@/composables/filters/eth-validator';
import type { StakingValidatorManage } from '@/composables/accounts/blockchain/use-account-manage';
import type { ContextColorsType, DataTableColumn } from '@rotki/ui-library';
import type { EthereumValidator, EthereumValidatorRequestPayload } from '@/types/blockchain/accounts';

const emit = defineEmits<{
  (e: 'edit', value: StakingValidatorManage): void;
}>();

const { t } = useI18n();

const { fetchValidators } = useBlockchainStore();
const { currencySymbol } = storeToRefs(useGeneralSettingsStore());
const { showConfirmation } = useAccountDelete();
const { fetchEthStakingValidators } = useEthStaking();

const {
  filters,
  matchers,
  state: rows,
  fetchData,
  pagination,
  sort,
} = usePaginationFilters<
  EthereumValidator,
  EthereumValidatorRequestPayload,
  EthereumValidator,
  Collection<EthereumValidator>,
  Filters,
  Matcher
>(
  null,
  true,
  () => useEthValidatorAccountFilter(t),
  fetchValidators,
  {
    defaultSortBy: {
      key: 'index',
    },
  },
);

const cols = computed<DataTableColumn<EthereumValidator>[]>(() => {
  const currency = { symbol: get(currencySymbol) };
  return [
    {
      label: t('common.validator_index'),
      key: 'index',
      sortable: true,
      cellClass: 'py-0',
    },
    {
      label: t('eth2_input.public_key'),
      key: 'publicKey',
      sortable: true,
      cellClass: 'py-0',
    },
    {
      label: t('common.status'),
      key: 'status',
      sortable: true,
      cellClass: 'py-0',
    },
    {
      label: t('common.amount'),
      key: 'amount',
      sortable: true,
      cellClass: 'py-0',
      align: 'end',
    },
    {
      label: t('account_balances.headers.usd_value', currency),
      key: 'usdValue',
      sortable: true,
      cellClass: 'py-0',
      align: 'end',
    },
    {
      label: t('common.ownership'),
      key: 'ownershipPercentage',
      sortable: false,
      cellClass: 'py-0',
      align: 'end',
    },
    {
      label: t('common.actions_text'),
      key: 'actions',
      cellClass: '!p-0',
      align: 'end',
    },
  ];
});

const { isTaskRunning } = useTaskStore();
const { isLoading } = useStatusStore();

const loading = isLoading(Section.BLOCKCHAIN, Blockchain.ETH2);

const accountOperation = logicOr(
  isTaskRunning(TaskType.ADD_ACCOUNT),
  isTaskRunning(TaskType.REMOVE_ACCOUNT),
  loading,
);

function edit(account: EthereumValidator) {
  const { publicKey, index, ownershipPercentage } = account;
  const state: StakingValidatorManage = {
    mode: 'edit',
    type: 'validator',
    chain: Blockchain.ETH2,
    data: {
      ownershipPercentage: ownershipPercentage ?? '100',
      publicKey,
      validatorIndex: index.toString(),
    },
  };
  emit('edit', state);
}

function getColor(status: string): ContextColorsType | undefined {
  switch (status) {
    case 'active':
      return 'success';
    case 'pending':
      return 'info';
    case 'exiting':
      return 'warning';
    case 'exited':
      return 'error';
    default:
      return undefined;
  }
}

function getOwnershipPercentage(row: EthereumValidator): string {
  return row.ownershipPercentage || '100';
}

async function refresh() {
  await fetchEthStakingValidators();
  await fetchData();
}

function confirmDelete(item: EthereumValidator) {
  showConfirmation({
    type: 'validator',
    data: item,
  }, () => {
    startPromise(fetchData());
  });
}

onMounted(async () => {
  await fetchData();
});
</script>

<template>
  <RuiCard>
    <template #header>
      <div class="flex flex-row items-center gap-2">
        <RefreshButton
          :disabled="loading"
          :loading="loading"
          :tooltip="t('account_balances.refresh_tooltip')"
          @refresh="refresh()"
        />
        <CardTitle class="ml-2">
          {{ t('blockchain_balances.validators') }}
        </CardTitle>
      </div>
    </template>
    <div class="flex w-full">
      <div class="grow" />
      <div>
        <TableFilter
          v-model:matches="filters"
          :matchers="matchers"
          class="max-w-[calc(100vw-11rem)] w-[25rem] lg:max-w-[30rem]"
          :location="SavedFilterLocation.ETH_VALIDATORS"
        />
      </div>
    </div>
    <RuiDataTable
      v-model:sort.external="sort"
      v-model:pagination.external="pagination"
      class="mt-4"
      dense
      row-attr="index"
      outlined
      :cols="cols"
      :rows="rows.data"
      sticky-header
      :empty="{ description: t('data_table.no_data') }"
    >
      <template #item.index="{ row }">
        <HashLink
          class="my-2"
          chain="eth2"
          type="address"
          :show-icon="false"
          :text="row.index.toString()"
        />
      </template>
      <template #item.publicKey="{ row }">
        <HashLink
          class="my-2"
          chain="eth2"
          type="address"
          :show-icon="false"
          :text="row.publicKey.toString()"
        />
      </template>
      <template #item.status="{ row }">
        <RuiChip
          size="sm"
          :color="getColor(row.status)"
        >
          {{ row.status }}
        </RuiChip>
      </template>
      <template #item.amount="{ row }">
        <AmountDisplay
          :value="row.amount"
          asset="ETH"
          :asset-padding="0.1"
        />
      </template>
      <template #item.usdValue="{ row }">
        <AmountDisplay
          fiat-currency="USD"
          :value="row.usdValue"
          show-currency="symbol"
        />
      </template>
      <template #item.ownershipPercentage="{ row }">
        <PercentageDisplay :value="getOwnershipPercentage(row)" />
      </template>
      <template #item.actions="{ row }">
        <div class="flex justify-end mr-2">
          <RowActions
            :edit-tooltip="t('account_balances.edit_tooltip')"
            :disabled="accountOperation"
            @edit-click="edit(row)"
            @delete-click="confirmDelete(row)"
          />
        </div>
      </template>
      <template #body.prepend="{ colspan }">
        <Eth2ValidatorLimitRow :colspan="colspan" />
      </template>
    </RuiDataTable>
  </RuiCard>
</template>
